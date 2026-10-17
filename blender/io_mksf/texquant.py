"""Palette work with numpy only (Blender has no PIL).

Images are uint8 arrays (h, w, 4), row 0 at the top.
"""
import numpy as np

from . import txd as txdmod

def palette_rgba(colors):
    return np.array([txdmod.bgr555_to_rgba(c) for c in colors], dtype=np.uint8)

def render(t, ti, pi):
    """Texture ti of TXD t through palette pi -> (h, w, 4) uint8."""
    idx = np.array(t.pixels(ti), dtype=np.int32)
    return palette_rgba(t.palettes[pi].colors)[idx]

def snap(img, colors):
    """Indices into an existing palette: nearest colour; alpha < 128 -> transparent entry if there is one."""
    pal = palette_rgba(colors).astype(np.int32)
    h, w = img.shape[:2]
    flat = img.reshape(-1, 4).astype(np.int32)
    transparent = np.nonzero(pal[:, 3] == 0)[0]
    opaque = np.nonzero(pal[:, 3] != 0)[0]
    cand = opaque if len(opaque) else np.arange(len(pal))
    uniq, inv = np.unique(flat[:, :3], axis=0, return_inverse=True)
    d = ((uniq[:, None, :] - pal[None, cand, :3]) ** 2).sum(-1)
    out = cand[d.argmin(1)][inv.reshape(-1)]
    if len(transparent):
        out[flat[:, 3] < 128] = transparent[0]
    return out.reshape(h, w)

def _median_cut(colors, counts, n):
    boxes = [(colors, counts)]
    while len(boxes) < n:
        best, score = None, -1
        for i, (c, k) in enumerate(boxes):
            if len(c) < 2:
                continue
            rng = c.max(0) - c.min(0)
            s = rng.max() * k.sum()
            if s > score:
                best, score = i, s
        if best is None:
            break
        c, k = boxes.pop(best)
        ch = (c.max(0) - c.min(0)).argmax()
        order = c[:, ch].argsort(kind="stable")
        c, k = c[order], k[order]
        cum = k.cumsum()
        cut = int(np.searchsorted(cum, cum[-1] / 2.0)) + 1
        cut = min(max(cut, 1), len(c) - 1)
        boxes += [(c[:cut], k[:cut]), (c[cut:], k[cut:])]
    return np.array([(c * k[:, None]).sum(0) / k.sum() for c, k in boxes])

def rebuild(images, ncolors, stp):
    """New palette for several images together -> (BGR555 list of ncolors, [index arrays]).
    Transparent pixels (alpha < 128) get index 0 = 0x0000."""
    alpha = any((im[..., 3] < 128).any() for im in images)
    first = 1 if alpha else 0
    opaque = np.concatenate([im.reshape(-1, 4)[im.reshape(-1, 4)[:, 3] >= 128, :3] for im in images])
    if len(opaque) == 0:
        opaque = np.zeros((1, 3), np.uint8)
    # Quantize in 15-bit space: what the PS1 can show anyway.
    q = (opaque.astype(np.int32) >> 3)
    uniq, counts = np.unique(q, axis=0, return_counts=True)
    if len(uniq) <= ncolors - first:
        cols = uniq.astype(np.float64)
    else:
        cols = _median_cut(uniq.astype(np.float64), counts.astype(np.float64), ncolors - first)
    cols = np.clip(np.rint(cols), 0, 31).astype(np.int32)
    # 0x0000 is the transparent colour: opaque black without STP becomes 0x0421 (8, 8, 8).
    pal = ([0] if alpha else []) + [int(r | (g << 5) | (b << 10) | (stp << 15)) or 0x0421 for r, g, b in cols]
    pal += [pal[-1]] * (ncolors - len(pal))
    out = []
    for im in images:
        idx = snap(im, pal) if not alpha else _snap_reserved(im, pal)
        out.append(idx)
    return pal, out

def _snap_reserved(im, pal):
    """Like snap, but entry 0 is only for transparent pixels."""
    rgba = palette_rgba(pal).astype(np.int32)
    flat = im.reshape(-1, 4).astype(np.int32)
    uniq, inv = np.unique(flat[:, :3], axis=0, return_inverse=True)
    d = ((uniq[:, None, :] - rgba[None, 1:, :3]) ** 2).sum(-1)
    out = (d.argmin(1) + 1)[inv.reshape(-1)]
    out[flat[:, 3] < 128] = 0
    return out.reshape(im.shape[:2])
