"""Play as another character: rebuild jaxsk (the player model) from a donor model, e.g. jarek.

  python3 scripts/swap_player.py jarek "MKSF Mod.bin" [--source ORIGINAL.bin] [--order 0,1,...]

The player is always jaxsk.skn with the jaxsk.tex dictionary, which every level TXD holds at fixed
VRAM spots. The donor's textures are packed into those spots: every spot becomes 8bpp (same VRAM
bytes: a 4bpp 48-wide texture is a 8bpp 24-wide one), palette 0 becomes the donor's palette and the
donor's textures are cropped to what its faces use, scaled down as little as needed and packed.
Body parts are reordered to Jax's skeleton (by default by matching pivots; Jax lists the right
forearm before the right bicep).
"""
import argparse
import math
import os
import sys
import types

import numpy as np
from PIL import Image

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "blender", "io_mksf")
pkg = types.ModuleType("io_mksf")
pkg.__path__ = [PKG]
sys.modules["io_mksf"] = pkg
from io_mksf import cdf, disc, modapply, texquant          # noqa: E402
from io_mksf import skn as sknmod                          # noqa: E402

PLAYER = "jaxsk"

def group_order(player, donor):
    """donor group index for each player group index, matched by mirrored side and position."""
    used, order = set(), []
    for pg in player.groups:
        best = min((gi for gi in range(len(donor.groups)) if gi not in used),
                   key=lambda gi: (abs(donor.groups[gi][0] - pg[0]) * 3 + abs(donor.groups[gi][1] - pg[1])
                                   + abs(donor.groups[gi][2] - pg[2])))
        used.add(best)
        order.append(best)
    return order

def _maxrects(rects, bins, order):
    free = [[(0, 0, bw, bh)] for bw, bh in bins]
    out = [None] * len(rects)
    for i in order:
        best = None
        for rot in (False, True):
            w, h = rects[i][::-1] if rot else rects[i]
            for b, fl in enumerate(free):
                for (x, y, fw, fh) in fl:
                    if w <= fw and h <= fh:
                        score = (min(fw - w, fh - h), max(fw - w, fh - h))
                        if best is None or score < best[0]:
                            best = (score, b, x, y, w, h, rot)
        if best is None:
            return None
        _, b, x, y, w, h, rot = best
        out[i] = (b, x, y, rot)
        nf = []
        for (fx, fy, fw, fh) in free[b]:
            if x >= fx + fw or x + w <= fx or y >= fy + fh or y + h <= fy:
                nf.append((fx, fy, fw, fh))
                continue
            if x > fx:
                nf.append((fx, fy, x - fx, fh))
            if x + w < fx + fw:
                nf.append((x + w, fy, fx + fw - x - w, fh))
            if y > fy:
                nf.append((fx, fy, fw, y - fy))
            if y + h < fy + fh:
                nf.append((fx, y + h, fw, fy + fh - y - h))
        free[b] = [r for r in nf if not any(r != o and r[0] >= o[0] and r[1] >= o[1] and
                                            r[0] + r[2] <= o[0] + o[2] and r[1] + r[3] <= o[1] + o[3] for o in nf)]
    return out

def pack(rects, bins):
    """MaxRects (best short side fit, 90-degree rotation allowed). -> [(bin, x, y, rotated)] or None."""
    n = range(len(rects))
    for key in (lambda i: -rects[i][0] * rects[i][1], lambda i: -max(rects[i]), lambda i: -min(rects[i]),
                lambda i: -rects[i][1], lambda i: -rects[i][0]):
        out = _maxrects(rects, bins, sorted(n, key=key))
        if out:
            return out
    return None

def build(sf_src, sf_dst, donor_name, order=None):
    player = sknmod.SKN(cdf.get(sf_dst, PLAYER + ".skn"))
    donor = sknmod.SKN(cdf.get(sf_src, donor_name + ".skn"))
    dname = donor.name.split(b"\0")[0].decode()
    _, dt = modapply.find_dict(sf_src, dname)
    _, pt = modapply.find_dict(sf_dst, PLAYER)
    if dt is None:
        raise SystemExit("no TXD with dictionary " + dname)
    dtr, dpr = dt.dict_range(dname)
    ptr, ppr = pt.dict_range(PLAYER)
    if len(dpr) != 1:
        print("note: donor has %d palettes, all its textures are mapped through palette 0" % len(dpr))
    pal = dt.palettes[dpr.start].colors
    if len(pal) != 256 or len(pt.palettes[ppr.start].colors) != 256:
        raise SystemExit("palette 0 of both dictionaries must have 256 colours")

    # Slots: every texture of the player's dictionary that owns pixels, as an 8bpp rectangle.
    slots = []
    for k, ti in enumerate(ptr):
        t = pt.textures[ti]
        if t.src >= 0:
            continue
        slots.append((k, t.width // 2 if t.is4 else t.width, t.height))

    # Donor textures cropped to the used UVs.
    def clamp_uv(f, c):
        t = dt.textures[dtr.start + f[4]]
        return min(f[6][2 * c], t.width - 1), min(f[6][2 * c + 1], t.height - 1)

    crop = {}
    for f in donor.faces:
        nc = 3 if f[3] == f[2] else 4          # a triangle's 4th UV is garbage
        us = [clamp_uv(f, c)[0] for c in range(nc)]
        vs = [clamp_uv(f, c)[1] for c in range(nc)]
        c = crop.setdefault(f[4], [255, 255, 0, 0])
        c[0], c[1] = min(c[0], *us), min(c[1], *vs)
        c[2], c[3] = max(c[2], *us), max(c[3], *vs)
    texs = sorted(crop)
    sizes = [(crop[t][2] - crop[t][0] + 1, crop[t][3] - crop[t][1] + 1) for t in texs]
    scale, place = 1.0, None
    while scale > 0.2:
        rects = [(max(1, math.ceil(w * scale)), max(1, math.ceil(h * scale))) for w, h in sizes]
        place = pack(rects, [(w, h) for _, w, h in slots])
        if place:
            break
        scale -= 0.01
    if not place:
        raise SystemExit("cannot pack the donor's textures")
    print("donor textures scaled to %.2f" % scale)

    # Pixels of every slot (palette 0 = donor palette).
    canvases = [np.zeros((h, w, 4), np.uint8) for _, w, h in slots]
    remap = {}
    for i, t in enumerate(texs):
        b, x, y, rot = place[i]
        w, h = rects[i]
        u0, v0, u1, v1 = crop[t]
        img = texquant.render(dt, dtr.start + t, dpr.start)[v0:v1 + 1, u0:u1 + 1]
        small = np.array(Image.fromarray(img, "RGBA").resize((w, h), Image.LANCZOS if (w, h) != img.shape[1::-1] else Image.NEAREST))
        if rot:
            small = small.transpose(1, 0, 2)    # stored with u and v swapped
        sh, sw = small.shape[:2]
        canvases[b][y:y + sh, x:x + sw] = small
        sx = (w - 1) / max(1, u1 - u0)
        sy = (h - 1) / max(1, v1 - v0)
        remap[t] = (slots[b][0], x, y, u0, v0, sx, sy, rot)
    rows, formats = {}, {}
    for (k, w, h), cv in zip(slots, canvases):
        rows[k] = texquant.snap(cv, pal).tolist()
        formats[k] = (w, False)

    # Geometry: donor parts in the player's skeleton order.
    order = order or group_order(player, donor)
    print("body parts:", ", ".join("%s<-%s" % (player.group_name(i), donor.group_name(g)) for i, g in enumerate(order)))
    owner = donor.vertex_group()
    new_index, verts, groups = {}, [], []
    for pi, gi in enumerate(order):
        g = donor.groups[gi]
        members = [v for v in range(len(donor.verts)) if owner[v] == gi]
        groups.append(list(g[:3]) + [len(members), len(verts), g[5], player.groups[pi][6]])
        for v in members:
            new_index[v] = len(verts)
            verts.append(donor.verts[v])
    faces = []
    for f in donor.faces:
        k, x, y, u0, v0, sx, sy, rot = remap[f[4]]
        uv = []
        nc = 3 if f[3] == f[2] else 4
        for c in range(4):
            cc = min(c, nc - 1)
            cu, cv = clamp_uv(f, cc)
            du, dv = round((cu - u0) * sx), round((cv - v0) * sy)
            uv += [x + dv, y + du] if rot else [x + du, y + dv]
        faces.append([new_index[v] for v in f[:4]] + [k, 0, tuple(uv)])
    out = sknmod.SKN()
    out.name, out.ntex, out.npal = player.name, player.ntex, player.npal
    out.verts, out.faces, out.groups = verts, faces, groups
    return modapply.Edit(PLAYER + ".skn", out.build(), PLAYER, rows, {0: list(pal)}, formats)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("donor")
    ap.add_argument("target", help="mod disc image (.bin/.cue) to write into")
    ap.add_argument("--source", help="disc image to take the donor from (default: the target)")
    ap.add_argument("--order", help="donor group index for each player group, comma separated")
    a = ap.parse_args()
    target = disc.resolve_bin(a.target)
    if os.path.basename(target).lower() in ("mortal kombat - special forces (usa).bin", "mortal kombat sf s prototype.bin"):
        raise SystemExit("refusing to write an original image")
    d = disc.Disc(target, writable=True)
    sf = d.read_file("SF.CDF")
    sf_src = sf
    if a.source:
        s = disc.Disc(disc.resolve_bin(a.source))
        sf_src = s.read_file("SF.CDF")
        s.close()
    order = [int(x) for x in a.order.split(",")] if a.order else None
    edit = build(sf_src, sf, a.donor, order)
    new_sf = modapply.apply(sf, [edit], log=lambda s: None)
    d.replace_file("SF.CDF", new_sf)
    d.close()
    d = disc.Disc(target)
    assert d.read_file("SF.CDF") == new_sf
    d.close()
    print("written and verified:", target)
