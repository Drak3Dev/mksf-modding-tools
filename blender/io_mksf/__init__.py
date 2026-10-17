"""Mortal Kombat: Special Forces (PS1) character models for Blender.

File > Import > MKSF model: pick a disc image (.cue/.bin), an SF.CDF or a loose .skn, then the model.
File > Export > MKSF model to disc image: writes the selected models (and their textures) into a mod
disc image. The image is created from the source disc on first export; later exports update it in
place, so earlier mods on it stay.
"""
import hashlib
import json
import os
import shutil

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import bsp as bspmod
from . import cdf, disc, entities_blender, level_blender, modapply, skn_blender
from . import skn as sknmod
from . import txd as txdmod

# Original images: never written to.
PROTECTED = {"mortal kombat - special forces (usa).bin", "mortal kombat sf s prototype.bin"}

_models_cache = {}

def read_exe(path):
    """The release EXE from a disc image, or None."""
    if not path.lower().endswith((".bin", ".cue")):
        return None
    d = disc.Disc(disc.resolve_bin(path))
    try:
        return d.read_file("SLUS_008.24")
    except KeyError:
        return None
    finally:
        d.close()

def read_sf(path):
    p = path.lower()
    if p.endswith((".bin", ".cue")):
        d = disc.Disc(disc.resolve_bin(path))
        try:
            return d.read_file("SF.CDF")
        finally:
            d.close()
    return open(path, "rb").read()

def import_model(context, source, model, report):
    if source.lower().endswith(".skn"):
        data = open(source, "rb").read()
        t = None
        folder = os.path.dirname(source)
        name = sknmod.SKN(data).name.split(b"\0")[0].decode("latin-1")
        for f in sorted(os.listdir(folder)):
            if f.lower().endswith(".txd"):
                try:
                    tt = txdmod.TXD(open(os.path.join(folder, f), "rb").read())
                    tt.dict_range(name)
                    t = tt
                    break
                except Exception:
                    continue
        model = os.path.splitext(os.path.basename(source))[0]
    else:
        sf = read_sf(source)
        data = cdf.get(sf, model + ".skn")
        name = sknmod.SKN(data).name.split(b"\0")[0].decode("latin-1")
        tname, t = modapply.find_dict(sf, name)
    obj = skn_blender.build_object(model, data, t, source=os.path.abspath(source))
    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    if t is None:
        report({"WARNING"}, "%s: no texture dictionary found, imported without textures" % model)
    report({"INFO"}, "MKSF: imported %s (%d vertices, %d faces)" % (model, len(obj.data.vertices), len(obj.data.polygons)))
    return obj

def import_level(context, source, level, report):
    if source.lower().endswith(".bsp"):
        data = open(source, "rb").read()
        level = os.path.splitext(os.path.basename(source))[0]
        t = None
        folder = os.path.dirname(source)
        name = bspmod.BSP(data).dict_name()
        for f in sorted(os.listdir(folder)):
            if f.lower().endswith(".txd"):
                try:
                    tt = txdmod.TXD(open(os.path.join(folder, f), "rb").read())
                    tt.dict_range(name)
                    t = tt
                    break
                except Exception:
                    continue
    else:
        sf = read_sf(source)
        data = cdf.get(sf, level + ".bsp")
        tname, t = modapply.find_dict(sf, bspmod.BSP(data).dict_name())
    if source.lower().endswith(".bsp"):
        sf = None
    obj, n, skipped = level_blender.build_level(level, data, t, source=os.path.abspath(source))
    exe = read_exe(source)
    if exe is not None and sf is not None and not bspmod.BSP(data).proto:
        try:
            counts = entities_blender.build_entities(exe, sf, level + ".bsp", obj)
            report({"INFO"}, "MKSF: entities: " + ", ".join("%s %d" % kv for kv in counts.items()))
        except Exception as e:
            report({"WARNING"}, "MKSF: entities not imported: %s" % e)
    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    if t is None:
        report({"WARNING"}, "%s: no texture dictionary found, imported without textures" % level)
    report({"INFO"}, "MKSF: imported level %s (%d polygons, %d degenerate skipped)" % (level, n, skipped))
    return obj

KINDS = {"model": (".skn", import_model), "level": (".bsp", import_level)}

class MKSF_OT_pick(bpy.types.Operator):
    """Choose what to import from the archive"""
    bl_idname = "mksf.pick"
    bl_label = "MKSF: choose"
    bl_property = "item"
    bl_options = {"REGISTER", "UNDO"}

    source: StringProperty(options={"HIDDEN"})
    kind: StringProperty(options={"HIDDEN"})

    def items(self, context):
        return [(m, m, "") for m in _models_cache.get((self.source, self.kind), [])]

    item: EnumProperty(items=items)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        try:
            KINDS[self.kind][1](context, self.source, self.item, self.report)
        except Exception as e:
            self.report({"ERROR"}, "MKSF: %s" % e)
            return {"CANCELLED"}
        return {"FINISHED"}

def import_or_pick(op, context, kind, name):
    ext, func = KINDS[kind]
    src = op.filepath
    if src.lower().endswith((".skn", ".bsp")) or name:
        try:
            func(context, src, name, op.report)
        except Exception as e:
            op.report({"ERROR"}, "MKSF: %s" % e)
            return {"CANCELLED"}
        return {"FINISHED"}
    try:
        names = sorted(n[:-4] for n, o, s in cdf.read(read_sf(src))[2] if n.lower().endswith(ext))
    except Exception as e:
        op.report({"ERROR"}, "MKSF: cannot read %s: %s" % (src, e))
        return {"CANCELLED"}
    if not names:
        op.report({"ERROR"}, "MKSF: nothing of type %s in %s" % (ext, src))
        return {"CANCELLED"}
    _models_cache[(src, kind)] = names
    bpy.ops.mksf.pick("INVOKE_DEFAULT", source=src, kind=kind)
    return {"FINISHED"}

class IMPORT_SCENE_OT_mksf(bpy.types.Operator, ImportHelper):
    """Import a Mortal Kombat: Special Forces character model"""
    bl_idname = "import_scene.mksf"
    bl_label = "Import MKSF model"
    bl_options = {"REGISTER", "UNDO"}

    filter_glob: StringProperty(default="*.cue;*.bin;*.cdf;*.CDF;*.skn", options={"HIDDEN"})
    model: StringProperty(name="Model", description="Model name (e.g. jaxsk); empty = choose from a list")

    def execute(self, context):
        return import_or_pick(self, context, "model", self.model)

class IMPORT_SCENE_OT_mksf_level(bpy.types.Operator, ImportHelper):
    """Import a Mortal Kombat: Special Forces level (.bsp geometry)"""
    bl_idname = "import_scene.mksf_level"
    bl_label = "Import MKSF level"
    bl_options = {"REGISTER", "UNDO"}

    filter_glob: StringProperty(default="*.cue;*.bin;*.cdf;*.CDF;*.bsp", options={"HIDDEN"})
    level: StringProperty(name="Level", description="Level name (e.g. office1); empty = choose from a list")

    def execute(self, context):
        return import_or_pick(self, context, "level", self.level)

def mksf_objects(context, selected_only):
    objs = context.selected_objects if selected_only else context.scene.objects
    out = [o for o in objs if o.type == "MESH" and "mksf" in o]
    for f, g in level_groups(context, objs).items():
        out.append(g["objs"][0])
    return out

def level_groups(context, objs):
    """{bsp file: (level objects + plain meshes with its materials, collision object or None)} for every level
    touched by objs (a level object, a part of it, an entity or its collision object)."""
    files = {}
    for o in context.scene.objects:
        if "mksf_level" in o:
            info = level_blender.info_of(o)
            files.setdefault(info["file"].lower(), {"dict": info["dict"], "objs": [], "coll": None})["objs"].append(o)
    by_dict = {v["dict"].lower(): k for k, v in files.items()}
    for o in context.scene.objects:
        if o.type != "MESH" or "mksf_level" in o or "mksf" in o or "mksf_ent" in o:
            continue
        if any(c.name == "MKSF models" for c in o.users_collection):
            continue
        if "mksf_collision" in o:
            f = by_dict.get(str(o["mksf_collision"]).lower())
            if f:
                files[f]["coll"] = o
            continue
        for m in o.data.materials:
            if m is not None and "mksf_tex" in m:
                d = m.name.split("_t")[0].lower()
                if d in by_dict:
                    files[by_dict[d]]["objs"].append(o)
                    break
    touched = set()
    for o in objs:
        if "mksf_level" in o:
            touched.add(level_blender.info_of(o)["file"].lower())
        elif "mksf_ent" in o:
            touched.add(json.loads(o["mksf_ent"])["bsp"].lower())
        elif "mksf_collision" in o:
            f = by_dict.get(str(o["mksf_collision"]).lower())
            if f:
                touched.add(f)
        else:
            for f, v in files.items():
                if o in v["objs"]:
                    touched.add(f)
    return {f: files[f] for f in touched if f in files}

def source_of(o):
    return (skn_blender.info_of(o) if "mksf" in o else level_blender.info_of(o)).get("source", "")

def export_to_disc(context, target, objs, base, report):
    target = disc.resolve_bin(target)
    if os.path.basename(target).lower() in PROTECTED:
        raise ValueError("%s is an original disc image; export into a copy (e.g. 'MKSF Mod.bin')" % os.path.basename(target))
    if not os.path.exists(target):
        base = disc.resolve_bin(base) if base else ""
        if not base or not os.path.exists(base) or not base.lower().endswith(".bin"):
            raise ValueError("target does not exist and no source disc image to copy it from")
        shutil.copyfile(base, target)
        disc.write_cue(target)
        report({"INFO"}, "MKSF: created %s from %s" % (os.path.basename(target), os.path.basename(base)))
    d = disc.Disc(target, writable=True)
    try:
        sf = d.read_file("SF.CDF")
        dg = context.evaluated_depsgraph_get()
        edits, refresh = [], []
        overlays = {}
        assigns = []
        bases_to_set = []
        colls = []
        exe_data = d.read_file("SLUS_008.24") if any(x.path == "SLUS_008.24" for x in d.entries()) else None
        warn = lambda m: report({"WARNING"}, "MKSF: " + m)
        for o in objs:
            if "mksf_level" in o:
                info = level_blender.info_of(o)
                grp = level_groups(context, [o])[info["file"].lower()]
                cur = cdf.get(sf, info["file"])
                bases = {level_blender.info_of(x).get("base") for x in grp["objs"] if "mksf_level" in x} - {None}
                if len(bases) > 1:
                    raise ValueError("%s: parts imported from different versions of the level; re-import it once"
                                     % info["file"])
                base = next(iter(bases), None)
                if base and hashlib.md5(cur).hexdigest() != base:
                    src = None
                    try:
                        src = cdf.get(read_sf(info["source"]), info["file"])
                    except Exception:
                        pass
                    if src is not None and hashlib.md5(src).hexdigest() == base:
                        cur = src
                        report({"INFO"}, "MKSF: %s on the target differs from the imported one; the level is "
                               "rebuilt from %s" % (info["file"], os.path.basename(info["source"])))
                    else:
                        raise ValueError("%s on the target is not the version this .blend was imported from; "
                                         "re-import the level from the target disc" % info["file"])
                colls_all = [x for x in context.scene.objects
                             if str(x.get("mksf_collision", "")).lower() == info["dict"].lower()]
                if len(colls_all) > 1:
                    report({"WARNING"}, "MKSF: several collision objects for %s (%s); using %s" % (
                        info["file"], ", ".join(x.name for x in colls_all), grp["coll"].name))
                if len(grp["objs"]) > 1:
                    report({"INFO"}, "MKSF: %s: %d objects exported as one level" % (info["file"], len(grp["objs"])))
                new, upd, add, rem = level_blender.build_bsp(grp["objs"], cur, dg, warn=warn, coll_obj=grp["coll"])
                assigns.append(level_blender.LAST_ASSIGN)
                bases_to_set.append((grp["objs"], info["file"]))
                if grp["coll"] is not None:
                    colls.append((grp["coll"], info["file"]))
                if exe_data is not None:
                    new = entities_blender.apply_entities(info["file"], new, sf, overlays, exe_data, warn=warn)
                tname, t = modapply.find_dict(sf, info["dict"])
                texs, pals = level_blender.texture_edits(o, t, new, log=warn) if t else ({}, {})
                edits.append(modapply.Edit(info["file"], None if new == cur else new, info["dict"], texs, pals))
                refresh.append((o, info["dict"], level_blender.refresh_images))
                report({"INFO"}, "MKSF: %s: %d polygons kept, %d added, %d removed; textures %s, palettes %s" % (
                    info["file"], upd, add, rem, sorted(texs) or "-", sorted(pals) or "-"))
                continue
            info = skn_blender.info_of(o)
            m = skn_blender.build_skn(o, dg, warn=warn)
            name = bytes.fromhex(info["name"]).split(b"\0")[0].decode("latin-1")
            tname, t = modapply.find_dict(sf, name)
            texs, pals = skn_blender.texture_edits(o, t, m, log=warn) if t else ({}, {})
            same = m.same(sknmod.SKN(cdf.get(sf, info["model"] + ".skn")))
            edits.append(modapply.Edit(info["model"] + ".skn", None if same else m.build(), name, texs, pals))
            refresh.append((o, name, skn_blender.refresh_images))
            report({"INFO"}, "MKSF: %s: geometry %s, textures %s, palettes %s" % (
                info["model"], "unchanged" if same else "changed", sorted(texs) or "-", sorted(pals) or "-"))
        for name, ovl in overlays.items():
            if bytes(ovl.data) != cdf.get(sf, name):
                edits.append(modapply.Edit(name, bytes(ovl.data)))
                report({"INFO"}, "MKSF: %s: entities updated" % name)
        lines = []
        new_sf = modapply.apply(sf, edits, log=lines.append)
        if new_sf == sf:
            report({"INFO"}, "MKSF: nothing changed")
            return
        d.replace_file("SF.CDF", new_sf, log=lambda s: report({"INFO"}, "MKSF: " + s))
    finally:
        d.close()
    d = disc.Disc(target)
    try:
        if d.read_file("SF.CDF") != new_sf:
            raise ValueError("verification failed: SF.CDF on the disc differs")
    finally:
        d.close()
    def safe(what, fn, *a):
        try:
            fn(*a)
        except Exception as e:
            report({"WARNING"}, "MKSF: %s failed after writing (%s)" % (what, e))
    for a in assigns:
        safe("storing polygon numbers", level_blender.store_assignment, a, lambda m: report({"WARNING"}, "MKSF: " + m))
    for objs_, f in bases_to_set:
        safe("level version mark", level_blender.set_base, objs_, cdf.get(new_sf, f))
    for co, f in colls:
        safe("collision refresh", level_blender.refresh_collision, co, bspmod.BSP(cdf.get(new_sf, f)))
    for o, name, func in refresh:
        tname, t = modapply.find_dict(new_sf, name)
        if t is not None:
            safe("texture preview refresh", func, o, t)
    report({"INFO"}, "MKSF: written and verified %s (%d copies updated)" % (os.path.basename(target), len(lines)))

class EXPORT_SCENE_OT_mksf_disc(bpy.types.Operator, ExportHelper):
    """Write MKSF models and levels into a mod disc image (created from the source disc if missing)"""
    bl_idname = "export_scene.mksf_disc"
    bl_label = "Export MKSF to disc image"

    filename_ext = ".bin"
    filter_glob: StringProperty(default="*.bin;*.cue", options={"HIDDEN"})
    selected_only: BoolProperty(name="Selected only", default=True)
    base: StringProperty(name="Source disc", description="Copied when the target does not exist yet "
                         "(default: the disc the model was imported from)")

    def invoke(self, context, event):
        objs = mksf_objects(context, True) or mksf_objects(context, False)
        if objs and not self.filepath:
            src = source_of(objs[0])
            folder = os.path.dirname(src) if src else ""
            proto = "proto" in os.path.basename(src).lower()
            self.filepath = os.path.join(folder, "MKSF Proto Mod.bin" if proto else "MKSF Mod.bin")
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        objs = mksf_objects(context, self.selected_only)
        if not objs and self.selected_only and context.view_layer.objects.active is not None:
            sel = [o.select_get() for o in context.scene.objects]
            context.view_layer.objects.active.select_set(True)
            objs = mksf_objects(context, True)
        if not objs:
            objs = mksf_objects(context, False)          # nothing usable selected: everything in the scene
        if not objs:
            self.report({"ERROR"}, "MKSF: no MKSF models or levels in the scene")
            return {"CANCELLED"}
        base = self.base or source_of(objs[0])
        try:
            export_to_disc(context, self.filepath, objs, base, self.report)
        except Exception as e:
            self.report({"ERROR"}, "MKSF: %s" % e)
            return {"CANCELLED"}
        return {"FINISHED"}

class EXPORT_SCENE_OT_mksf_skn(bpy.types.Operator, ExportHelper):
    """Write the active MKSF model as a loose .skn file (geometry only)"""
    bl_idname = "export_scene.mksf_skn"
    bl_label = "Export MKSF .skn"

    filename_ext = ".skn"
    filter_glob: StringProperty(default="*.skn", options={"HIDDEN"})

    def execute(self, context):
        o = context.active_object
        if o is None or "mksf" not in o:
            self.report({"ERROR"}, "MKSF: the active object is not an MKSF model")
            return {"CANCELLED"}
        try:
            m = skn_blender.build_skn(o, context.evaluated_depsgraph_get(), warn=lambda s: self.report({"WARNING"}, s))
        except Exception as e:
            self.report({"ERROR"}, "MKSF: %s" % e)
            return {"CANCELLED"}
        open(self.filepath, "wb").write(m.build())
        self.report({"INFO"}, "MKSF: %d vertices, %d faces -> %s" % (len(m.verts), len(m.faces), self.filepath))
        return {"FINISHED"}

def ensure_collision_object(context, level_obj):
    info = level_blender.info_of(level_obj)
    for x in context.scene.objects:
        if str(x.get("mksf_collision", "")).lower() == info["dict"].lower():
            return x
    data = cdf.get(read_sf(info["source"]), info["file"])
    return level_blender.build_collision(info["dict"], bspmod.BSP(data))

def plain_selection(context):
    return [o for o in context.selected_objects if o.type == "MESH" and "mksf_ent" not in o
            and "mksf_collision" not in o and "mksf" not in o
            and not any(c.name == "MKSF models" for c in o.users_collection)]

class MKSF_OT_object_collision(bpy.types.Operator):
    """Make the selected objects solid in the game: copy their faces into the level's collision object
    (walls and floors by face direction; ceilings are skipped). Faces that already have collision in the same
    place are not added twice"""
    bl_idname = "mksf.object_collision"
    bl_label = "Create object collision"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(plain_selection(context))

    def execute(self, context):
        import bmesh
        sel = plain_selection(context)
        groups = level_groups(context, sel)
        if not groups:
            self.report({"ERROR"}, "MKSF: the selection does not belong to an imported level "
                        "(use the level's materials or a part of the level)")
            return {"CANCELLED"}
        added = skipped = 0
        for f, g in groups.items():
            level_obj = next(o for o in g["objs"] if "mksf_level" in o)
            try:
                co = g["coll"] or ensure_collision_object(context, level_obj)
            except Exception as e:
                self.report({"ERROR"}, "MKSF: cannot load the collision of %s: %s" % (f, e))
                return {"CANCELLED"}
            mwc = co.matrix_world
            existing = set()
            for p in co.data.polygons:
                existing.add(tuple(sorted(tuple(round(c) for c in (mwc @ co.data.vertices[i].co) * 100)
                                          for i in p.vertices)))
            bm = bmesh.new()
            bm.from_mesh(co.data)
            cq = bm.faces.layers.int.get("mksf_cq") or bm.faces.layers.int.new("mksf_cq")
            ct = bm.faces.layers.int.get("mksf_ct") or bm.faces.layers.int.new("mksf_ct")
            inv = mwc.inverted()
            for o in sel:
                if o not in g["objs"]:
                    continue
                mw = o.matrix_world
                nm = mw.to_3x3()
                for p in o.data.polygons:
                    n = (nm @ p.normal).normalized()
                    if n.z < -0.7 or len(p.vertices) not in (3, 4):
                        continue
                    world = [mw @ o.data.vertices[i].co for i in p.vertices]
                    key = tuple(sorted(tuple(round(c) for c in w * 100) for w in world))
                    if key in existing:
                        skipped += 1
                        continue
                    existing.add(key)
                    try:
                        nf = bm.faces.new([bm.verts.new(inv @ w) for w in world])
                    except ValueError:
                        continue
                    nf[cq] = 0
                    nf[ct] = 2 if n.z > 0.7 else 1
                    added += 1
            bm.to_mesh(co.data)
            bm.free()
            co.data.update()
        if added:
            self.report({"INFO"}, "MKSF: %d collision faces created%s; export to make them solid" % (
                added, " (%d already had collision)" % skipped if skipped else ""))
        else:
            self.report({"WARNING"}, "MKSF: collision already exists here (%d faces), nothing added" % skipped)
        return {"FINISHED"}

class MKSF_PT_panel(bpy.types.Panel):
    bl_label = "MKSF"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MKSF"

    def draw(self, context):
        lay = self.layout
        lay.operator(IMPORT_SCENE_OT_mksf.bl_idname, icon="IMPORT")
        lay.operator(IMPORT_SCENE_OT_mksf_level.bl_idname, icon="IMPORT")
        lay.operator(EXPORT_SCENE_OT_mksf_disc.bl_idname, icon="DISC")
        if plain_selection(context):
            lay.operator(MKSF_OT_object_collision.bl_idname, icon="MOD_PHYSICS")
        o = context.active_object
        if o is None or "mksf" not in o:
            return
        info = skn_blender.info_of(o)
        box = lay.box()
        box.label(text="%s: %d verts / %d faces" % (info["model"], len(o.data.vertices), len(o.data.polygons)))
        box.label(text="limits: %d / %d, %d per body part" % (skn_blender.LIMITS["verts"], skn_blender.LIMITS["faces"],
                                                              skn_blender.LIMITS["group_verts"]))
        box.label(text="from: %s" % os.path.basename(info.get("source", "")))

@bpy.app.handlers.persistent
def pack_dirty_images(*args):
    """Textures live packed inside the .blend: repack edited ones before every save."""
    for img in bpy.data.images:
        if img.is_dirty and (img.packed_file is not None or img.source == "GENERATED"):
            try:
                img.pack()
            except RuntimeError:
                pass

def menu_import(self, context):
    self.layout.operator(IMPORT_SCENE_OT_mksf.bl_idname, text="MKSF model (PS1 .cue/.bin/.cdf/.skn)")
    self.layout.operator(IMPORT_SCENE_OT_mksf_level.bl_idname, text="MKSF level (PS1 .cue/.bin/.cdf/.bsp)")

def menu_export(self, context):
    self.layout.operator(EXPORT_SCENE_OT_mksf_disc.bl_idname, text="MKSF models/levels to disc image (.bin)")
    self.layout.operator(EXPORT_SCENE_OT_mksf_skn.bl_idname, text="MKSF model (.skn)")

classes = (MKSF_OT_object_collision, MKSF_OT_pick, IMPORT_SCENE_OT_mksf, IMPORT_SCENE_OT_mksf_level, EXPORT_SCENE_OT_mksf_disc, EXPORT_SCENE_OT_mksf_skn, MKSF_PT_panel)

def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)
    bpy.app.handlers.save_pre.append(pack_dirty_images)

def unregister():
    if pack_dirty_images in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(pack_dirty_images)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
