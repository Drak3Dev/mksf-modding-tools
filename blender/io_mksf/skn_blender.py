"""SKN <-> Blender object.

Blender axes: X = x, Y = z, Z = -y (PS1 y points down); 1 unit = 100 game units.
Body parts are vertex groups; materials carry mksf_tex / mksf_pal (texture and palette inside the
model's dictionary). Everything needed to write the model back is kept in obj["mksf"] (JSON).
"""
import json
import re

import bpy
import numpy as np

from . import skn as sknmod
from . import texquant

SCALE = 0.01
LIMITS = {"verts": 344, "faces": 435, "group_verts": 90}   # largest values among the game's own models

def to_blender(x, y, z):
    return (x * SCALE, z * SCALE, -y * SCALE)

def from_blender(co):
    return (round(co[0] / SCALE), round(-co[2] / SCALE), round(co[1] / SCALE))

def uv_to_blender(u, v, w, h):
    return ((u + 0.5) / w, 1.0 - (v + 0.5) / h)

def uv_from_blender(U, V, w, h):
    return (max(0, min(255, round(U * w - 0.5))), max(0, min(255, round((1.0 - V) * h - 0.5))))

# -------------------------------------------
# Images
# -------------------------------------------

def image_from_rgba(name, rgba):
    h, w = rgba.shape[:2]
    old = bpy.data.images.get(name)
    if old is not None:
        bpy.data.images.remove(old)
    img = bpy.data.images.new(name, w, h, alpha=True)
    img.pixels.foreach_set((rgba[::-1].astype(np.float32) / 255.0).reshape(-1))
    img.pack()
    return img

def rgba_from_image(img):
    w, h = img.size
    buf = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    return np.rint(buf.reshape(h, w, 4)[::-1] * 255.0).clip(0, 255).astype(np.uint8)

def primary_palettes(t, tr, pr, model):
    """{owner tex: palette} — the palette most faces use with each texture's pixels."""
    owners = [(t.textures[ti].src - tr.start) if t.textures[ti].src >= 0 else k for k, ti in enumerate(tr)]
    votes = {}
    for f in model.faces:
        if f[4] < len(owners):
            votes.setdefault(owners[f[4]], {}).setdefault(f[5], 0)
            votes[owners[f[4]]][f[5]] += 1
    prim = {}
    for k, ti in enumerate(tr):
        if owners[k] != k:
            continue
        if k in votes:
            prim[k] = max(votes[k], key=votes[k].get)
        else:
            want = 16 if t.textures[ti].is4 else 256
            prim[k] = next((p for p, pi in enumerate(pr) if len(t.palettes[pi].colors) == want), 0)
    return owners, prim

def make_material(name, img, tex, pal):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat["mksf_tex"], mat["mksf_pal"] = tex, pal
    try:
        mat.use_nodes = True
    except AttributeError:
        pass
    nt = mat.node_tree
    for n in list(nt.nodes):
        if n.type == "TEX_IMAGE":
            nt.nodes.remove(n)
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    if img is not None:
        node = nt.nodes.new("ShaderNodeTexImage")
        node.interpolation = "Closest"
        node.location = (-400, 200)
        node.image = img
        nt.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
        nt.links.new(node.outputs["Alpha"], bsdf.inputs["Alpha"])
    if "Roughness" in bsdf.inputs:
        bsdf.inputs["Roughness"].default_value = 1.0
    try:
        mat.blend_method = "CLIP"
    except AttributeError:
        pass
    return mat

# -------------------------------------------
# Import
# -------------------------------------------

def build_object(model_file, data, t=None, source=""):
    """Create the mesh object for SKN bytes; t = TXD containing the model's dictionary (or None)."""
    m = sknmod.SKN(data)
    name = m.name.split(b"\0")[0].decode("latin-1")
    owner = m.vertex_group()

    verts = [to_blender(*m.world(i, owner)) for i in range(len(m.verts))]
    faces, face_uv, face_key = [], [], []
    seen = set()
    for f in m.faces:
        idx, uvs = [], []
        for k in range(4):
            if f[k] not in idx:
                idx.append(f[k])
                uvs.append((f[6][2 * k], f[6][2 * k + 1]))
        key = frozenset(idx)
        if len(idx) < 3 or key in seen:
            continue
        seen.add(key)
        faces.append(idx[::-1])
        face_uv.append(uvs[::-1])
        face_key.append((f[4], f[5]))

    info = {"model": model_file, "name": m.name.hex(), "ntex": m.ntex, "npal": m.npal,
            "groups": [g[:6] + [g[6].hex()] for g in m.groups],
            "vpad": m.verts[0][3] if m.verts else 0, "source": source,
            "tex_sizes": [], "owners": [], "primary": {}, "images": {}}

    images = {}
    if t is not None:
        tr, pr = t.dict_range(name)
        info["tex_sizes"] = [[t.textures[ti].width, t.textures[ti].height] for ti in tr]
        owners, prim = primary_palettes(t, tr, pr, m)
        info["owners"] = owners
        info["primary"] = {str(k): v for k, v in prim.items()}
        for k, p in prim.items():
            img = image_from_rgba("%s_t%02d" % (name, k), texquant.render(t, tr.start + k, pr.start + p))
            images[(k, p)] = img
            info["images"][str(k)] = img.name
        for tk, pk in sorted(set(face_key)):
            if tk < len(owners) and (owners[tk], pk) not in images and (tk, pk) not in images:
                images[(tk, pk)] = image_from_rgba("%s_t%02d_p%d_view" % (name, tk, pk),
                                                   texquant.render(t, tr.start + tk, pr.start + pk))

    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    uvl = me.uv_layers.new(name="UVMap")
    mats = {}
    for p, poly in enumerate(me.polygons):
        tk, pk = face_key[p]
        if (tk, pk) not in mats:
            if t is not None and tk < len(info["owners"]):
                img = images.get((info["owners"][tk], pk)) or images.get((tk, pk))
            else:
                img = None
            me.materials.append(make_material("%s_t%02d_p%d" % (name, tk, pk), img, tk, pk))
            mats[(tk, pk)] = len(me.materials) - 1
        poly.material_index = mats[(tk, pk)]
        w, h = info["tex_sizes"][tk] if tk < len(info["tex_sizes"]) else (256, 256)
        for k, li in enumerate(poly.loop_indices):
            uvl.data[li].uv = uv_to_blender(face_uv[p][k][0], face_uv[p][k][1], w, h)

    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    for gi in range(len(m.groups)):
        vg = obj.vertex_groups.new(name=m.group_name(gi))
        members = [i for i in range(len(m.verts)) if owner[i] == gi]
        if members:
            vg.add(members, 1.0, "REPLACE")
    obj["mksf"] = json.dumps(info)
    return obj

# -------------------------------------------
# Export
# -------------------------------------------

def info_of(obj):
    if "mksf" not in obj:
        raise ValueError("%s was not imported by the MKSF add-on" % obj.name)
    return json.loads(obj["mksf"])

def material_key(mat):
    if mat is None:
        return None
    if "mksf_tex" in mat and "mksf_pal" in mat:
        return int(mat["mksf_tex"]), int(mat["mksf_pal"])
    m = re.search(r"t(\d+)_p(\d+)", mat.name)
    return (int(m.group(1)), int(m.group(2))) if m else None

def build_skn(obj, depsgraph, warn=print):
    """SKN bytes from the object (modifiers applied, object transform applied)."""
    info = info_of(obj)
    groups_t = [g[:6] + [bytes.fromhex(g[6])] for g in info["groups"]]
    gnames = [g[6].split(b"\0")[0].decode("latin-1") for g in groups_t]
    ev = obj.evaluated_get(depsgraph)
    me = ev.to_mesh()
    try:
        mw = obj.matrix_world
        vg_to_group = {vg.index: gnames.index(vg.name) for vg in obj.vertex_groups if vg.name in gnames}
        nv = len(me.vertices)
        group = [None] * nv
        for v in me.vertices:
            best = None
            for ge in v.groups:
                if ge.group in vg_to_group and ge.weight > 0 and (best is None or ge.weight > best[1]):
                    best = (vg_to_group[ge.group], ge.weight)
            if best:
                group[v.index] = best[0]
        for _ in range(16):
            if all(g is not None for g in group):
                break
            for p in me.polygons:
                gs = [group[i] for i in p.vertices if group[i] is not None]
                if gs:
                    for i in p.vertices:
                        if group[i] is None:
                            group[i] = max(set(gs), key=gs.count)
        used = set(i for p in me.polygons for i in p.vertices)
        lost = [i for i in used if group[i] is None]
        if lost:
            raise ValueError("%d vertices belong to no body part (vertex group), e.g. #%d" % (len(lost), lost[0]))

        order, groups = [], []
        for gi, g in enumerate(groups_t):
            members = [i for i in range(nv) if group[i] == gi and i in used]
            groups.append(g[:3] + [len(members), len(order)] + g[5:])
            order += members
        newidx = {old: new for new, old in enumerate(order)}
        verts = []
        for i in order:
            x, y, z = from_blender(mw @ me.vertices[i].co)
            g = groups_t[group[i]]
            lv = [x - g[0], y - g[1], z - g[2]]
            if not all(-32768 <= c <= 32767 for c in lv):
                raise ValueError("vertex %d is too far from its pivot" % i)
            verts.append(lv + [info["vpad"]])

        sizes = info["tex_sizes"]
        uvl = me.uv_layers.active
        if uvl is None:
            raise ValueError("the mesh has no UV map")
        faces = []
        for p in me.polygons:
            n = len(p.vertices)
            if n not in (3, 4):
                raise ValueError("polygon %d has %d corners: only triangles and quads (Ctrl+T to triangulate)" % (p.index, n))
            key = material_key(me.materials[p.material_index] if me.materials else None)
            if key is None:
                raise ValueError("polygon %d has no MKSF material" % p.index)
            tex, pal = key
            if tex >= info["ntex"] or pal >= info["npal"]:
                raise ValueError("material t%02d_p%d is outside the dictionary (%d textures, %d palettes)"
                                 % (tex, pal, info["ntex"], info["npal"]))
            w, h = sizes[tex] if tex < len(sizes) else (256, 256)
            idx = [newidx[i] for i in p.vertices][::-1]
            uvs = [uv_from_blender(uvl.data[li].uv[0], uvl.data[li].uv[1], w, h) for li in p.loop_indices][::-1]
            if n == 3:
                idx.append(idx[2])
                uvs.append(uvs[2])
            faces.append(idx + [tex, pal, tuple(c for uv in uvs for c in uv)])
    finally:
        ev.to_mesh_clear()

    out = sknmod.SKN()
    out.name, out.ntex, out.npal = bytes.fromhex(info["name"]), info["ntex"], info["npal"]
    out.verts, out.faces, out.groups = verts, faces, groups
    for k, lim, val in (("verts", LIMITS["verts"], len(verts)), ("faces", LIMITS["faces"], len(faces))):
        if val > lim:
            warn("%s: %d %s (largest in the game: %d) - untested" % (obj.name, val, k, lim))
    for gi, g in enumerate(groups):
        if g[3] > LIMITS["group_verts"]:
            warn("%s: body part %s has %d vertices (largest in the game: %d) - untested"
                 % (obj.name, gnames[gi], g[3], LIMITS["group_verts"]))
    return out

def texture_edits(obj, t, model, log=print):
    """({tex: rows}, {pal: colors}) for images that differ from TXD t (the target's current data)."""
    info = info_of(obj)
    name = bytes.fromhex(info["name"]).split(b"\0")[0].decode("latin-1")
    tr, pr = t.dict_range(name)
    prim = {int(k): v for k, v in info["primary"].items()}
    owners = info["owners"]
    imgs, changed = {}, set()
    for k, p in prim.items():
        img = bpy.data.images.get(info["images"].get(str(k), ""))
        if img is None:
            continue
        a = rgba_from_image(img)
        tex = t.textures[tr.start + k]
        if a.shape[:2] != (tex.height, tex.width):
            raise ValueError("image %s is %dx%d, must stay %dx%d" % (img.name, a.shape[1], a.shape[0], tex.width, tex.height))
        imgs[k] = a
        cur = texquant.render(t, tr.start + k, pr.start + p)
        same = np.array_equal(a[..., :3] >> 3, cur[..., :3] >> 3) and np.array_equal(a[..., 3] >= 128, cur[..., 3] >= 128)
        if not same:
            changed.add(k)
    texs, pals = {}, {}
    pairs = set((f[4], f[5]) for f in model.faces)
    for p in sorted(set(prim[k] for k in changed)):
        members = sorted(k for k in prim if prim[k] == p and k in imgs)
        exclusive = all(tk < len(owners) and prim.get(owners[tk]) == pal for tk, pal in pairs if pal == p)
        colors = t.palettes[pr.start + p].colors
        if exclusive:
            stp = 1 if all(c & 0x8000 for c in colors) else 0
            new_pal, rows = texquant.rebuild([imgs[k] for k in members], len(colors), stp)
            pals[p] = new_pal
            for k, r in zip(members, rows):
                texs[k] = r.tolist()
        else:
            for k in members:
                if k in changed:
                    texs[k] = texquant.snap(imgs[k], colors).tolist()
                    log("%s t%02d: palette %d is shared with other textures, colours snapped to it" % (name, k, p))
    return texs, pals

def refresh_images(obj, t):
    """Reload the object's images from TXD t (after export, to show what the PS1 will show)."""
    info = info_of(obj)
    name = bytes.fromhex(info["name"]).split(b"\0")[0].decode("latin-1")
    tr, pr = t.dict_range(name)
    for k, iname in info["images"].items():
        img = bpy.data.images.get(iname)
        if img is not None:
            rgba = texquant.render(t, tr.start + int(k), pr.start + info["primary"][k])
            if not img.has_data:
                img.reload()
            if tuple(img.size) != (rgba.shape[1], rgba.shape[0]) or len(img.pixels) != rgba.size:
                continue
            img.pixels.foreach_set((rgba[::-1].astype(np.float32) / 255.0).reshape(-1))
            img.pack()
