"""Level entities in Blender (release levels). Everything goes into the collection "<level> entities":

  enemy_NN <model>   the enemy's own model (shared mesh); props mksf_type, mksf_script, mksf_mode.
                     Position snaps to 256 game units on export (the game stores it that way).
  script_NN          poly curve through the patrol points (move-to and positioned calls), in order.
                     Move the points; the number of points is fixed by the script.
  pickup_NN <name>   the item's model; props mksf_code, mksf_arg.
  light_NN           point light; colour = the game's colour; props mksf_params (4 ints, kept).
  player_start       arrow; rotation around Z = start angle (only in the level's first BSP).

Every object carries mksf_ent = JSON {kind, overlay, area, index, bsp}. Export writes positions,
types, scripts, codes and colours back in place (no entities are added or removed).
"""
import json
import math

import bpy

from . import bsp as bspmod
from . import cdf
from . import levelents as le
from . import modapply
from . import skn as sknmod
from .skn_blender import build_object, from_blender, to_blender

def _collection(name, parent):
    c = bpy.data.collections.get(name)
    if c is None:
        c = bpy.data.collections.new(name)
        parent.children.link(c)
    return c

def _model_mesh(model_file, sf, cache, store):
    """Shared mesh of an .skn/.lvl model (None if it cannot be read)."""
    if model_file in cache:
        return cache[model_file]
    mesh = None
    try:
        data = cdf.get(sf, model_file)
        if model_file.lower().endswith(".skn"):
            name = sknmod.SKN(data).name.split(b"\0")[0].decode("latin-1")
            _, t = modapply.find_dict(sf, name)
            o = build_object(model_file[:-4], data, t)
            del o["mksf"]                    # a display copy, not an exportable model
            for c in list(o.users_collection):
                c.objects.unlink(o)
            store.objects.link(o)
            mesh = o.data
    except Exception:
        mesh = None
    cache[model_file] = mesh
    return mesh

def _tag(obj, kind, ovl, area, index, bsp_file):
    obj["mksf_ent"] = json.dumps({"kind": kind, "overlay": ovl, "area": area, "index": index, "bsp": bsp_file})

def _marker(name, coll, loc, shape="SPHERE", size=0.3):
    o = bpy.data.objects.new(name, None)
    o.empty_display_type = shape
    o.empty_display_size = size
    o.location = loc
    coll.objects.link(o)
    return o

def build_entities(exe_data, sf, bsp_file, level_obj):
    exe = le.Exe(exe_data)
    ovl, area = le.find_overlay(exe, lambda n: cdf.get(sf, n), bsp_file)
    b = bspmod.BSP(cdf.get(sf, bsp_file))
    root = _collection("%s entities" % bsp_file[:-4], bpy.context.scene.collection)
    store = _collection("MKSF models", bpy.context.scene.collection)
    store.hide_viewport = True
    store.hide_render = True
    cache = {}
    counts = {}

    for L in le.lights(b):
        ld = bpy.data.lights.new("light_%02d" % L["index"], "POINT")
        c = L["color"]
        ld.color = ((c & 255) / 255.0, ((c >> 8) & 255) / 255.0, ((c >> 16) & 255) / 255.0)
        ld.energy = 200
        lo = bpy.data.objects.new(ld.name, ld)
        lo.location = to_blender(*L["pos"])
        lo["mksf_params"] = list(L["params"])
        _tag(lo, "light", None, None, L["index"], bsp_file)
        root.objects.link(lo)
    counts["lights"] = len(le.lights(b))
    if ovl is None:
        return counts

    types = exe.enemy_types()
    items = exe.item_types()
    level_tex_ok = True
    enemies = ovl.enemies(area)
    used_scripts = set()
    for e in enemies:
        t = e["type"]
        model = types[t]["model"] if t < len(types) else "?"
        mesh = _model_mesh(model, sf, cache, store)
        name = "enemy_%02d %s" % (e["index"], model[:-4])
        o = bpy.data.objects.new(name, mesh) if mesh else _marker(name, root, (0, 0, 0), "CONE", 0.5)
        if mesh:
            root.objects.link(o)
        o.location = to_blender(*e["pos"])
        o["mksf_type"], o["mksf_script"], o["mksf_mode"] = t, e["script"], e["mode"]
        _tag(o, "enemy", ovl.name, area, e["index"], bsp_file)
        used_scripts.add(e["script"])
    counts["enemies"] = len(enemies)

    scripts = ovl.scripts()
    for si in sorted(used_scripts):
        if si >= len(scripts):
            continue
        pts = ovl.script_points(scripts[si])
        if not pts:
            continue
        cu = bpy.data.curves.new("script_%02d" % si, "CURVE")
        cu.dimensions = "3D"
        sp = cu.splines.new("POLY")
        sp.points.add(len(pts) - 1)
        for k, (_, p) in enumerate(pts):
            x, y, z = to_blender(*p)
            sp.points[k].co = (x, y, z, 1.0)
        sp.use_cyclic_u = scripts[si][-1][1] == 9
        cu.bevel_depth = 0.03
        co = bpy.data.objects.new(cu.name, cu)
        _tag(co, "script", ovl.name, area, si, bsp_file)
        root.objects.link(co)
    counts["scripts"] = len(used_scripts)

    # Pickups belong to the whole overlay; show those inside this BSP's root sphere.
    nodes = b.node_list()
    sx, sy, sz, sr = nodes[0].sphere
    n = 0
    for p in ovl.pickups():
        x, y, z = p["pos"]
        if (x - sx) ** 2 + (y - sy) ** 2 + (z - sz) ** 2 > (sr * 1.05) ** 2:
            continue
        info = items[p["code"]] if 0 <= p["code"] < len(items) else {"model": "?", "name": "?"}
        mesh = _model_mesh(info["model"], sf, cache, store)
        name = "pickup_%02d %s" % (p["index"], info["name"] or info["model"])
        o = bpy.data.objects.new(name, mesh) if mesh else _marker(name, root, (0, 0, 0), "CUBE", 0.25)
        if mesh:
            root.objects.link(o)
        o.location = to_blender(*p["pos"])
        o["mksf_code"], o["mksf_arg"] = p["code"], p["arg"]
        _tag(o, "pickup", ovl.name, area, p["index"], bsp_file)
        n += 1
    counts["pickups"] = n

    if area == 0:
        pos, ang = ovl.start()
        o = _marker("player_start", root, to_blender(*pos), "SINGLE_ARROW", 1.0)
        o.rotation_euler = (math.radians(-90), 0, -ang / 4096.0 * 2 * math.pi)
        _tag(o, "start", ovl.name, area, 0, bsp_file)
        counts["start"] = 1
    level_obj["mksf_entities"] = root.name
    return counts

def entity_objects(bsp_file):
    out = []
    for o in bpy.data.objects:
        if "mksf_ent" in o:
            info = json.loads(o["mksf_ent"])
            if info["bsp"].lower() == bsp_file.lower():
                out.append((o, info))
    return out

def apply_entities(bsp_file, bsp_data, sf, overlays, exe_data, warn=print):
    """Write entity objects of bsp_file into bsp_data (lights) and the overlays dict
    {name: levelents.Overlay}. Returns new bsp bytes."""
    ents = entity_objects(bsp_file)
    if not ents:
        return bsp_data
    exe = le.Exe(exe_data)
    descs = {ovl: desc for _, ovl, desc in exe.levels()}
    b = bspmod.BSP(bsp_data)
    nl = len(le.lights(b))
    for o, info in ents:
        kind = info["kind"]
        loc = from_blender(o.matrix_world.translation)
        if kind == "light":
            if info["index"] < nl and o.type == "LIGHT":
                c = o.data.color
                col = (round(c[0] * 255) & 255) | ((round(c[1] * 255) & 255) << 8) | ((round(c[2] * 255) & 255) << 16)
                le.set_light(b, info["index"], loc, col, list(o.get("mksf_params", [])) or None)
            continue
        name = info["overlay"]
        if name not in overlays:
            overlays[name] = le.Overlay(name, cdf.get(sf, name), descs[name])
        ovl = overlays[name]
        if kind == "enemy":
            ovl.set_enemy(info["area"], info["index"], loc, o.get("mksf_type"), o.get("mksf_script"), o.get("mksf_mode"))
        elif kind == "pickup":
            ovl.set_pickup(info["index"], loc, o.get("mksf_code"), o.get("mksf_arg"))
        elif kind == "start":
            ang = round(-o.rotation_euler[2] / (2 * math.pi) * 4096) % 4096
            ovl.set_start(loc, ang)
        elif kind == "script" and o.type == "CURVE":
            pts = ovl.script_points(ovl.scripts()[info["index"]])
            sp = o.data.splines[0]
            if len(sp.points) != len(pts):
                warn("%s: %d points, the script has %d; extra/missing points ignored" % (o.name, len(sp.points), len(pts)))
            mw = o.matrix_world
            for (addr, _), p in zip(pts, sp.points):
                ovl.set_point(addr, from_blender(mw @ p.co.xyz))
    return b.build()
