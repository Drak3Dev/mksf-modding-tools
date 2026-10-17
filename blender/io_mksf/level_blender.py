"""Level (.bsp) <-> Blender.

One mesh per level. Vertices with identical game coordinates are shared. Face attributes:
  mksf_poly     polygon number in the .bsp + 1 (0 = a face added in Blender)
  mksf_corners  which loop of the face is PS1 corner 0..3 (2 bits each)
Corner colours (baked lighting) are the colour attribute "Col". Materials show texture x colour x 2,
like the PS1 (0x80 = neutral), through an emission shader; they carry mksf_tex, mksf_pal, mksf_semi.

Export, against the level's current .bsp:
  * faces that still map to their polygon update it (positions, UVs, colours, texture/palette);
  * polygons whose face was deleted collapse to a point (the PS1 does not draw them);
  * new faces (triangles/quads) become new polygons in the BSP leaf containing their centre.
Collision is separate (not touched): moved walls stay solid where they were, new faces are not solid.
"""
import hashlib
import json
import struct

import bpy
import numpy as np

from . import bsp as bspmod
from . import texquant
from .skn_blender import from_blender, image_from_rgba, rgba_from_image, to_blender, uv_from_blender, uv_to_blender

NEUTRAL = 0x808080
LEAF_W2 = -32410          # r2.w of every leaf polygon in the release levels

def level_material(name, img, tex, pal, semi):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat["mksf_tex"], mat["mksf_pal"], mat["mksf_semi"] = tex, pal, int(semi)
    try:
        mat.use_nodes = True
    except AttributeError:
        pass
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    emit = nt.nodes.new("ShaderNodeEmission")
    emit.location = (200, 0)
    col = nt.nodes.new("ShaderNodeVertexColor")
    col.layer_name = "Col"
    col.location = (-400, -200)
    mul = nt.nodes.new("ShaderNodeMix")
    mul.data_type = "RGBA"
    mul.blend_type = "MULTIPLY"
    mul.inputs[0].default_value = 1.0
    mul.location = (-150, 0)
    dbl = nt.nodes.new("ShaderNodeMix")
    dbl.data_type = "RGBA"
    dbl.blend_type = "MULTIPLY"
    dbl.inputs[0].default_value = 1.0
    dbl.inputs[7].default_value = (2.0, 2.0, 2.0, 1.0)
    dbl.location = (50, 0)
    nt.links.new(col.outputs["Color"], mul.inputs[7])
    tex_node = None
    if img is not None:
        tex_node = nt.nodes.new("ShaderNodeTexImage")
        tex_node.interpolation = "Closest"
        tex_node.image = img
        tex_node.location = (-450, 150)
        nt.links.new(tex_node.outputs["Color"], mul.inputs[6])
    nt.links.new(mul.outputs[2], dbl.inputs[6])
    nt.links.new(dbl.outputs[2], emit.inputs["Color"])
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.location = (400, 0)
    transp = nt.nodes.new("ShaderNodeBsdfTransparent")
    transp.location = (200, -150)
    if semi:
        mix.inputs[0].default_value = 0.5
    elif tex_node is not None:
        nt.links.new(tex_node.outputs["Alpha"], mix.inputs[0])
    else:
        mix.inputs[0].default_value = 1.0
    nt.links.new(transp.outputs[0], mix.inputs[1])
    nt.links.new(emit.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    try:
        mat.blend_method = "BLEND" if semi else "CLIP"
    except AttributeError:
        pass
    return mat

PERIM = [0, 1, 3, 2]      # PS1 corner of each position around the polygon

def build_level(level_file, data, t=None, source=""):
    b = bspmod.BSP(data)
    name = b.dict_name()

    vindex, verts = {}, []
    faces, face_uv, face_col, face_key, face_poly, face_map = [], [], [], [], [], []
    skipped = 0
    owner = b.leaf_of_poly() if not b.proto else [0] * len(b.polys)
    for pi, q in enumerate(b.polys):
        if owner[pi] is None:
            continue                      # zone polygons (doors etc.): local coordinates, kept as they are
        cs = q.corners()
        idx, uvs, cols, pos_of = [], [], [], {}
        for j, pc in enumerate(PERIM):
            x, y, z, u, v, c = cs[pc]
            key = (x, y, z)
            if key not in vindex:
                vindex[key] = len(verts)
                verts.append(to_blender(x, y, z))
            vi = vindex[key]
            if vi not in idx:
                idx.append(vi)
                uvs.append((u, v))
                cols.append(c)
            pos_of[pc] = idx.index(vi)
        if len(idx) < 3:
            skipped += 1
            continue
        n = len(idx)
        cmap = 0
        for pc in range(4):
            cmap |= (n - 1 - pos_of[pc]) << (pc * 2)        # faces are reversed below
        semi = (q.colors[0] >> 24) & 2 != 0
        if not getattr(q, "textured", True):
            q_key = (-1, -1, semi)
        else:
            q_key = (q.tex, q.pal, semi)
        faces.append(idx[::-1])
        face_uv.append(uvs[::-1])
        face_col.append(cols[::-1])
        face_key.append(q_key)
        face_poly.append(pi + 1)
        face_map.append(cmap)

    sizes, images = {}, {}
    if t is not None:
        tr, pr = t.dict_range(name)
        sizes = {k: (t.textures[ti].width, t.textures[ti].height) for k, ti in enumerate(tr)}
        for tk, pk, semi in set(face_key):
            if 0 <= tk < len(tr) and 0 <= pk < len(pr) and (tk, pk) not in images:
                images[(tk, pk)] = image_from_rgba("%s_t%03d_p%03d" % (name, tk, pk),
                                                   texquant.render(t, tr.start + tk, pr.start + pk))

    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    uvl = me.uv_layers.new(name="UVMap")
    col = me.color_attributes.new(name="Col", type="BYTE_COLOR", domain="CORNER")
    me.color_attributes.active_color = col
    pattr = me.attributes.new(name="mksf_poly", type="INT", domain="FACE")
    mattr = me.attributes.new(name="mksf_corners", type="INT", domain="FACE")
    mats = {}
    uv_data = np.zeros((len(me.loops), 2), dtype=np.float32)
    col_data = np.zeros((len(me.loops), 4), dtype=np.float32)
    for p, poly in enumerate(me.polygons):
        key = face_key[p]
        if key not in mats:
            mname = ("%s_t%03d_p%03d" % (name, key[0], key[1])) if key[0] >= 0 else ("%s_colour" % name)
            me.materials.append(level_material(mname + ("_semi" if key[2] else ""),
                                               images.get(key[:2]), key[0], key[1], key[2]))
            mats[key] = len(me.materials) - 1
        poly.material_index = mats[key]
        w, h = sizes.get(key[0], (256, 256))
        for k, li in enumerate(poly.loop_indices):
            uv_data[li] = uv_to_blender(face_uv[p][k][0], face_uv[p][k][1], w, h)
            c = face_col[p][k]
            col_data[li] = ((c & 0xFF) / 255.0, ((c >> 8) & 0xFF) / 255.0, ((c >> 16) & 0xFF) / 255.0, 1.0)
    uvl.data.foreach_set("uv", uv_data.reshape(-1))
    col.data.foreach_set("color_srgb", col_data.reshape(-1))
    pattr.data.foreach_set("value", np.array(face_poly, dtype=np.int32))
    mattr.data.foreach_set("value", np.array(face_map, dtype=np.int32))

    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    if not b.proto:
        build_collision(name, b)
    obj["mksf_level"] = json.dumps({
        "file": level_file + ".bsp", "dict": name, "source": source, "polys": len(b.polys), "proto": b.proto,
        "base": hashlib.md5(data).hexdigest(),
        "tex_sizes": {str(k): v for k, v in sizes.items()},
        "images": {"%d_%d" % k: img.name for k, img in images.items()}})
    return obj, len(b.polys), skipped

def info_of(obj):
    return json.loads(obj["mksf_level"])

def set_base(objs, data):
    """Remember which .bsp the objects' polygon numbers refer to."""
    h = hashlib.md5(data).hexdigest()
    for o in objs:
        if "mksf_level" in o:
            info = info_of(o)
            info["base"] = h
            o["mksf_level"] = json.dumps(info)

def material_key(mat):
    if mat is None or "mksf_tex" not in mat:
        return None
    return int(mat["mksf_tex"]), int(mat["mksf_pal"]), bool(mat.get("mksf_semi", 0))

def _color(c):
    r, g, b = (max(0, min(255, round(x * 255))) for x in c[:3])
    return r | (g << 8) | (b << 16)

LAST_ASSIGN = []
SPLIT_EDGE = 320        # new/moved faces longer than this are cut into tiles

def _newell(pts):
    n = [0.0, 0.0, 0.0]
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        n[0] += (a[1] - b[1]) * (a[2] + b[2])
        n[1] += (a[2] - b[2]) * (a[0] + b[0])
        n[2] += (a[0] - b[0]) * (a[1] + b[1])
    return n

def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))

def _mix(a, b, t):
    """Interpolate two corners (pos, u, v, rgb)."""
    pos = tuple(int(round(a[0][k] + (b[0][k] - a[0][k]) * t)) for k in range(3))
    u = int(round(a[1] + (b[1] - a[1]) * t))
    v = int(round(a[2] + (b[2] - a[2]) * t))
    rgb = 0
    for sh in (0, 8, 16):
        ca, cb = (a[3] >> sh) & 255, (b[3] >> sh) & 255
        rgb |= int(round(ca + (cb - ca) * t)) << sh
    return (pos, u, v, rgb)

def _dist(a, b):
    return sum((a[0][k] - b[0][k]) ** 2 for k in range(3)) ** 0.5

def _subdivide(per, n):
    """Cut a perimeter (4 corners; a triangle repeats its last) into pieces with edges <= SPLIT_EDGE."""
    import math
    if n == 4:
        ns = max(1, math.ceil(max(_dist(per[0], per[1]), _dist(per[3], per[2])) / 256.0))
        nt = max(1, math.ceil(max(_dist(per[0], per[3]), _dist(per[1], per[2])) / 256.0))
        def at(i, j):
            s_, t_ = i / ns, j / nt
            return _mix(_mix(per[0], per[1], s_), _mix(per[3], per[2], s_), t_)
        grid = [[at(i, j) for j in range(nt + 1)] for i in range(ns + 1)]
        return [[grid[i][j], grid[i + 1][j], grid[i + 1][j + 1], grid[i][j + 1]] for i in range(ns) for j in range(nt)]
    tris = [per[:3]]
    for _ in range(4):
        out, again = [], False
        for a, b, c in tris:
            if max(_dist(a, b), _dist(b, c), _dist(c, a)) > SPLIT_EDGE:
                ab, bc, ca = _mix(a, b, .5), _mix(b, c, .5), _mix(c, a, .5)
                out += [[a, ab, ca], [ab, b, bc], [ca, bc, c], [ab, bc, ca]]
                again = True
            else:
                out.append([a, b, c])
        tris = out
        if not again:
            break
    return [t + [t[2]] for t in tris]
MAX_EDGE = 768          # game units; PS1 drops a polygon whose corner lands far off screen

def _mesh_faces(obj, depsgraph):
    """[(poly id or -1, corner map, [(pos, u, v, rgb or None)], material key, world normal, max edge)]"""
    ev = obj.evaluated_get(depsgraph)
    me = ev.to_mesh()
    out = []
    try:
        mw = obj.matrix_world
        nm = mw.to_3x3()
        n_loops = len(me.loops)
        pa = me.attributes.get("mksf_poly")
        ma = me.attributes.get("mksf_corners")
        polyid = np.zeros(len(me.polygons), dtype=np.int32)
        cmaps = np.zeros(len(me.polygons), dtype=np.int32)
        if pa is not None and pa.domain == "FACE":
            pa.data.foreach_get("value", polyid)
        if ma is not None and ma.domain == "FACE":
            ma.data.foreach_get("value", cmaps)
        uvl = me.uv_layers.active
        colattr = me.color_attributes.get("Col")
        cols = np.zeros((n_loops, 4), dtype=np.float32)
        has_col = colattr is not None and colattr.domain == "CORNER"
        if has_col:
            colattr.data.foreach_get("color_srgb", cols.reshape(-1))
        pos = [from_blender(mw @ v.co) for v in me.vertices]
        for p in me.polygons:
            key = material_key(me.materials[p.material_index] if me.materials else None)
            if key is None:
                raise ValueError("%s: face %d has no MKSF level material" % (obj.name, p.index))
            corner = []
            for li in p.loop_indices:
                uv = tuple(uvl.data[li].uv) if uvl else (0.5, 0.5)
                corner.append((pos[me.loops[li].vertex_index], uv, _color(cols[li]) if has_col else None))
            vs = [c[0] for c in corner]
            edge = max(sum((vs[i][k] - vs[i - 1][k]) ** 2 for k in range(3)) ** 0.5 for i in range(len(vs)))
            out.append((int(polyid[p.index]) - 1, int(cmaps[p.index]), corner, key, (nm @ p.normal).normalized(), edge,
                        p.index))
    finally:
        ev.to_mesh_clear()
    return out

def build_bsp(objs, data, depsgraph, warn=print, coll_obj=None):
    """New .bsp bytes from all objects of one level (level objects, separated parts, plain meshes with
    the level's materials), based on the level's current file data."""
    info = info_of(objs[0]) if "mksf_level" in objs[0] else None
    b = bspmod.BSP(data)
    if b.proto:
        raise ValueError("writing prototype levels is not supported yet")
    nodes = b.node_list()
    owner = b.leaf_of_poly(nodes)
    sizes = {}
    for o in objs:
        if "mksf_level" in o:
            sizes.update({int(k): v for k, v in info_of(o)["tex_sizes"].items()})
    leaf_w2 = next((q.r[2][3] for q, o in zip(b.polys, owner) if o is not None), LEAF_W2)
    old = {pid: [r[:3] for r in q.r] for pid, q in enumerate(b.polys)}

    used, added, new_coll, moved = set(), [], [], []
    updated, big, flipped, split = 0, 0, 0, 0
    assign = []                                   # (object, face index, Poly, corner map, new?)

    def make(cs, tex, pal, semi, q=None):
        """Poly from PS1-ordered corners cs = [(pos, u, v, rgb)] x 4."""
        if q is None:
            q = bspmod.Poly()
            q.r = [[0, 0, 0, 0] for _ in range(4)]
            q.r[2][3], q.r[3][3] = leaf_w2, 0
        for k, rk in enumerate((0, 1, 3, 2)):
            (x, y, z), u, v, rgb = cs[k]
            if not all(-32768 <= c <= 32767 for c in (x, y, z)):
                raise ValueError("a face is out of the 16-bit coordinate range")
            q.r[rk][0:3] = [x, y, z]
        q.r[1][3] = cs[2][1] | (cs[2][2] << 8)
        q.r[0][3] = cs[3][1] | (cs[3][2] << 8)
        for k in (0, 1):
            if q.r[k][3] > 32767:
                q.r[k][3] -= 65536
        q.uv0 = cs[0][1] | (cs[0][2] << 8)
        q.uv1 = cs[1][1] | (cs[1][2] << 8)
        q.tex, q.pal = tex, pal
        code = 0x3E if semi else 0x3C
        q.colors = [cs[k][3] | (code << 24) for k in range(4)]
        return q

    for obj in objs:
        for pid, cmap, corner, key, wn, edge, fidx in _mesh_faces(obj, depsgraph):
            n = len(corner)
            if n not in (3, 4):
                raise ValueError("%s: a face has %d corners: only triangles and quads (Ctrl+T)" % (obj.name, n))
            tex, pal, semi = key
            w, h = sizes.get(tex, (256, 256))
            corner = [(c[0],) + uv_from_blender(c[1][0], c[1][1], w, h) + (c[2],) for c in corner]
            if edge > MAX_EDGE:
                big += 1
            rev = corner[::-1]
            if n == 3:
                rev.append(rev[2])
            existing = 0 <= pid < len(b.polys) and pid not in used and owner[pid] is not None and \
                all(((cmap >> (2 * k)) & 3) < n for k in range(4))
            if existing:
                q0 = b.polys[pid]
                cs = [corner[(cmap >> (2 * k)) & 3] for k in range(4)]
                cs = [(c[0], c[1], c[2], c[3] if c[3] is not None else q0.colors[k] & 0xFFFFFF) for k, c in enumerate(cs)]
                per = [cs[0], cs[1], cs[3], cs[2]]
                if _dot(_newell([c[0] for c in per]), _newell([c[0] for c in rev])) < 0:
                    # winding no longer matches the Blender face (flipped normals, mirrored object)
                    per = [(c[0], c[1], c[2], c[3] if c[3] is not None else q0.colors[0] & 0xFFFFFF) for c in rev]
                    flipped += 1
                changed = sorted(tuple(c[0]) for c in per) != sorted(map(tuple, old[pid]))
            else:
                per = [(c[0], c[1], c[2], c[3] if c[3] else NEUTRAL) for c in rev]
                cmap = (n - 1) | ((n - 2) << 2) | ((n - 4 if n == 4 else n - 3) << 4) | ((n - 3) << 6)
                changed = True
            pieces = _subdivide(per, n) if changed and edge > SPLIT_EDGE else [per]
            if existing and len(pieces) == 1:
                used.add(pid)
                q = make([per[0], per[1], per[3], per[2]], tex, pal, semi, q0)
                assign.append((obj, fidx, q, cmap, False))
                updated += 1
                if [r[:3] for r in q.r] != old[pid]:
                    moved.append(pid)
                continue
            if len(pieces) > 1:
                split += 1
            for i, pc in enumerate(pieces):
                q = make([pc[0], pc[1], pc[3], pc[2]], tex, pal, semi)
                added.append(q)
                if i == 0:
                    assign.append((obj, fidx, q, cmap if len(pieces) == 1 else -1, True))
    if flipped:
        warn("%d faces had their winding reversed in Blender; exported facing the way Blender shows them" % flipped)
    if split:
        warn("%d large faces were cut into tiles (max %d game units) so the PS1 does not drop them" % (split, SPLIT_EDGE))
    if big:
        warn("%d faces have edges longer than %d game units (%.1f in Blender); the PS1 may drop them at close "
             "range - subdivide them" % (big, MAX_EDGE, MAX_EDGE / 100.0))

    removed = sum(1 for pid in range(len(b.polys)) if pid not in used and owner[pid] is not None)

    if True:                                       # always: also repairs spheres of older exports
        leaf_polys, zone_polys = {}, []
        moved_set = set(moved)
        for pid, (q, o) in enumerate(zip(b.polys, owner)):
            if o is None:
                zone_polys.append(q)
                continue
            if pid not in used:
                continue                              # deleted in Blender: dropped from the file
            if pid in moved_set:
                c = [sum(r[k] for r in q.r) / 4.0 for k in range(3)]
                o = b.leaf_path(c, nodes)[-1]
            leaf_polys.setdefault(o, []).append(q)
        for q in added:
            c = [sum(r[k] for r in q.r) / 4.0 for k in range(3)]
            leaf_polys.setdefault(b.leaf_path(c, nodes)[-1], []).append(q)
        b.set_polys(leaf_polys, zone_polys, nodes, grow=added + [b.polys[i] for i in moved])
    nodes = b.node_list()

    walls, floors = [], []
    if coll_obj is not None:
        cw, cf = apply_collision_object(b, coll_obj, depsgraph, nodes)
        walls += cw
        floors += cf
    if walls or floors:
        bspmod.add_collision(b, walls, floors, nodes)
        warn("%d new walls and %d new floors are solid" % (len(walls), len(floors)))
    index = {id(q): i for i, q in enumerate(b.polys)}
    global LAST_ASSIGN
    LAST_ASSIGN = [(o, f, index[id(q)], cm, nw) for o, f, q, cm, nw in assign]
    return b.build(), updated, len(added), removed

def store_assignment(assign, warn=print):
    """After a successful export: write the final polygon numbers back into the meshes, so the next export
    updates these polygons instead of adding them again."""
    by_obj = {}
    for o, f, i, cm, nw in assign:
        by_obj.setdefault(o, []).append((f, i, cm, nw))
    for o, lst in by_obj.items():
        me = o.data
        if len(me.polygons) != len(lst):
            warn("%s: modifiers change its faces; re-import the level before exporting it again" % o.name)
            continue
        for name in ("mksf_poly", "mksf_corners"):
            if me.attributes.get(name) is None:
                me.attributes.new(name, "INT", "FACE")
        pa = np.zeros(len(me.polygons), dtype=np.int32)
        ca = np.zeros(len(me.polygons), dtype=np.int32)
        for f, i, cm, nw in lst:
            pa[f] = i + 1 if cm >= 0 else 0          # cut into tiles: re-cut from the face on every export
            ca[f] = max(cm, 0)
        me.attributes["mksf_poly"].data.foreach_set("value", pa)
        me.attributes["mksf_corners"].data.foreach_set("value", ca)
        try:
            if me.color_attributes.get("Col") is None:
                me.color_attributes.new("Col", "BYTE_COLOR", "CORNER")
            col = me.color_attributes.get("Col")
            if col.domain == "CORNER" and len(col.data) == len(me.loops):
                c = np.zeros(len(me.loops) * 4, dtype=np.float32)
                col.data.foreach_get("color_srgb", c)
                c = c.reshape(-1, 4)
                for f, i, cm, nw in lst:
                    if nw:
                        for li in me.polygons[f].loop_indices:
                            if not c[li, :3].any():
                                c[li] = (128 / 255.0, 128 / 255.0, 128 / 255.0, 1.0)
                col.data.foreach_set("color_srgb", c.reshape(-1))
        except Exception as e:
            warn("%s: vertex colours not updated (%s)" % (o.name, e))
        me.update()

def _coll_entry(b, nodes, pts, bn):
    """(entry bytes, leaf numbers, is floor) for a face with world normal bn (Blender axes); None for ceilings."""
    if bn.z < -0.7:
        return None
    out = (bn.x, -bn.z, bn.y)                 # game-space normal towards the open side
    leaves = set()
    c = [sum(p_[k] for p_ in pts) / len(pts) for k in range(3)]
    for p_ in list(pts) + [c]:
        probe = [p_[k] + out[k] * 24 for k in range(3)]
        leaves.add(nodes[b.leaf_path(probe, nodes)[-1]].leaf)
    floor = bn.z > 0.7
    return bspmod.collision_quad(list(pts)[::-1], [-v for v in out], not floor), leaves, floor

# ---------------------------------------------------------------------------
# Collision object: faces = the level's collision quads (C1 walls, C2 floors).
# Face attributes mksf_cq = quad index + 1 (0 = new face), mksf_ct = 1 walls / 2 floors.
# ---------------------------------------------------------------------------

def collision_mesh(name, b):
    lay = bspmod._tail_layout(b.tail)
    verts, faces, cq, ct = [], [], [], []
    for t, tab in ((1, "C1"), (2, "C2")):
        o, n, size = lay[tab]
        for i in range(n):
            v = struct.unpack_from("<16h", b.tail, o + 4 + i * size)
            pts = [v[k * 4:k * 4 + 3] for k in range(4)]
            uniq = []
            for p in pts:
                if p not in uniq:
                    uniq.append(p)
            if len(uniq) < 3:
                continue
            faces.append(list(range(len(verts), len(verts) + len(uniq))))
            verts += [to_blender(*p) for p in uniq]
            cq.append(i + 1)
            ct.append(t)
    me = bpy.data.meshes.new(name + "_collision")
    me.from_pydata(verts, [], faces)
    me.update()
    a = me.attributes.new("mksf_cq", "INT", "FACE")
    a.data.foreach_set("value", np.array(cq, dtype=np.int32))
    a = me.attributes.new("mksf_ct", "INT", "FACE")
    a.data.foreach_set("value", np.array(ct, dtype=np.int32))
    mat = bpy.data.materials.get("MKSF collision") or bpy.data.materials.new("MKSF collision")
    mat.diffuse_color = (1.0, 0.2, 0.1, 0.35)
    me.materials.append(mat)
    return me

def refresh_collision(obj, b):
    """Replace the collision object's mesh with what the .bsp now contains."""
    old = obj.data
    obj.data = collision_mesh(obj["mksf_collision"], b)
    obj.matrix_world.identity()
    if old.users == 0:
        bpy.data.meshes.remove(old)

def build_collision(name, b, parent_coll=None):
    me = collision_mesh(name, b)
    obj = bpy.data.objects.new(name + "_collision", me)
    obj["mksf_collision"] = name
    obj.display_type = "WIRE"
    obj.hide_render = True
    (parent_coll or bpy.context.scene.collection).objects.link(obj)
    return obj

def apply_collision_object(b, obj, depsgraph, nodes):
    """Update/remove existing quads from the collision object; return new (walls, floors) entries."""
    lay = bspmod._tail_layout(b.tail)
    t = bytearray(b.tail)
    ev = obj.evaluated_get(depsgraph)
    me = ev.to_mesh()
    walls, floors = [], []
    try:
        mw = obj.matrix_world
        nm = mw.to_3x3()
        cq = np.zeros(len(me.polygons), dtype=np.int32)
        ct = np.zeros(len(me.polygons), dtype=np.int32)
        if me.attributes.get("mksf_cq") is not None:
            me.attributes["mksf_cq"].data.foreach_get("value", cq)
            me.attributes["mksf_ct"].data.foreach_get("value", ct)
        seen = {1: set(), 2: set()}
        for p in me.polygons:
            pts = [from_blender(mw @ me.vertices[i].co) for i in p.vertices]
            bn = (nm @ p.normal).normalized()
            idx, tab = int(cq[p.index]) - 1, int(ct[p.index])
            if idx >= 0 and tab in (1, 2) and idx not in seen[tab] and len(pts) in (3, 4):
                seen[tab].add(idx)
                o = lay["C%d" % tab][0] + 4 + idx * 48
                v = struct.unpack_from("<16h", t, o)
                old = [v[k * 4:k * 4 + 3] for k in range(4)]
                cur = pts + [pts[-1]] * (4 - len(pts))
                if sorted(map(tuple, cur)) != sorted(map(tuple, old)):
                    e = _coll_entry(b, nodes, pts, bn) or (bspmod.collision_quad(pts[::-1], (-bn.x, bn.z, -bn.y), True), set(), False)
                    t[o:o + 32] = e[0][:32]          # corners
                    t[o + 32:o + 40] = e[0][32:40]   # plane
                    struct.pack_into("<h", t, o + 42, struct.unpack_from("<h", e[0], 42)[0])   # flags
                continue
            if len(pts) not in (3, 4):
                raise ValueError("%s: collision faces must be triangles or quads" % obj.name)
            e = _coll_entry(b, nodes, pts, bn)
            if e:
                (floors if e[2] else walls).append(e[:2])
        for tab in (1, 2):
            o0, n, _ = lay["C%d" % tab]
            for i in range(n):
                if i in seen[tab]:
                    continue
                o = o0 + 4 + i * 48
                v = struct.unpack_from("<16h", t, o)
                if len({v[k * 4:k * 4 + 3] for k in range(4)}) < 3:
                    continue                       # already degenerate in the game data
                x, y, z = v[0:3]
                struct.pack_into("<16h", t, o, *([x, y, z, 0] * 4))   # removed: zero size, never hit
    finally:
        ev.to_mesh_clear()
    b.tail = bytes(t)
    return walls, floors

def texture_edits(obj, t, data, log=print):
    """({tex: rows}, {pal: colors}) for level images that differ from TXD t.
    A palette is rebuilt when only one texture uses it in this level; otherwise colours snap to it."""
    info = info_of(obj)
    name = info["dict"]
    tr, pr = t.dict_range(name)
    b = bspmod.BSP(data)
    users, pals_of = {}, {}
    for q in b.polys:
        users.setdefault(q.pal, set()).add(q.tex)
        pals_of.setdefault(q.tex, set()).add(q.pal)
    owner_of = lambda tk: (t.textures[tr.start + tk].src - tr.start) if t.textures[tr.start + tk].src >= 0 else tk

    imgs, changed = {}, []
    for key, iname in info["images"].items():
        tk, pk = (int(x) for x in key.split("_"))
        img = bpy.data.images.get(iname)
        if img is None:
            continue
        a = rgba_from_image(img)
        tex = t.textures[tr.start + tk]
        if a.shape[:2] != (tex.height, tex.width):
            raise ValueError("image %s is %dx%d, must stay %dx%d" % (iname, a.shape[1], a.shape[0], tex.width, tex.height))
        imgs[(tk, pk)] = a
        cur = texquant.render(t, tr.start + tk, pr.start + pk)
        if not (np.array_equal(a[..., :3] >> 3, cur[..., :3] >> 3) and np.array_equal(a[..., 3] >= 128, cur[..., 3] >= 128)):
            changed.append((tk, pk))

    texs, pals = {}, {}
    for pk in sorted(set(p for _, p in changed)):
        members = sorted(users.get(pk, set()))
        # Rebuild the palette when every texture using it uses only it, has its own pixels and an image here.
        rebuild = all(pals_of[m] == {pk} and owner_of(m) == m and (m, pk) in imgs for m in members) and \
            not any(owner_of(o) in members for o in range(len(tr)) if o not in members and o in pals_of)
        colors = t.palettes[pr.start + pk].colors
        if rebuild:
            stp = 1 if all(c & 0x8000 for c in colors) else 0
            new_pal, rows = texquant.rebuild([imgs[(m, pk)] for m in members], len(colors), stp)
            pals[pk] = new_pal
            for m, r in zip(members, rows):
                texs[m] = r.tolist()
        else:
            for tk, p in changed:
                if p != pk:
                    continue
                owner = owner_of(tk)
                if owner in texs:
                    log("%s: texture %d edited in more than one image; t%03d_p%03d ignored" % (name, owner, tk, pk))
                    continue
                texs[owner] = texquant.snap(imgs[(tk, pk)], colors).tolist()
                log("%s: palette %d is shared with other textures, colours of t%03d snapped to it" % (name, pk, tk))
    return texs, pals

def refresh_images(obj, t):
    info = info_of(obj)
    tr, pr = t.dict_range(info["dict"])
    for key, iname in info["images"].items():
        tk, pk = (int(x) for x in key.split("_"))
        img = bpy.data.images.get(iname)
        if img is not None:
            rgba = texquant.render(t, tr.start + tk, pr.start + pk)
            if not img.has_data:
                img.reload()
            if tuple(img.size) != (rgba.shape[1], rgba.shape[0]) or len(img.pixels) != rgba.size:
                continue
            img.pixels.foreach_set((rgba[::-1].astype(np.float32) / 255.0).reshape(-1))
            img.pack()
