"""Putting edited files into SF.CDF (no bpy here).

An Edit carries a file's new bytes (or None) and texture edits for one TXD dictionary.
apply() writes them into every copy: the root SF.CDF file, every level .cdf that contains the file,
and every TXD that contains the dictionary.
"""
from . import cdf
from . import txd as txdmod

class Edit:
    def __init__(self, file, data=None, dict_name=None, texs=None, pals=None, formats=None):
        self.file = file              # e.g. "jaxsk.skn", "warehse1.bsp"
        self.data = data
        self.dict = dict_name         # e.g. "jaxsk", "warehse1"
        self.texs = texs or {}        # {local tex index: rows of palette indices}
        self.pals = pals or {}        # {local pal index: BGR555 list}
        self.formats = formats or {}  # {local tex index: (width, is4)} -- same VRAM bytes, e.g. 4bpp 48 -> 8bpp 24

def find_dict(sf, dict_name):
    """First (name, TXD) in SF.CDF containing the dictionary, or (None, None)."""
    for n, o, s in cdf.read(sf)[2]:
        if n.lower().endswith(".txd"):
            try:
                t = txdmod.TXD(sf[o:o + s])
                t.dict_range(dict_name)
                return n, t
            except Exception:
                continue
    return None, None

def apply(sf, edits, log=print):
    name, total, files = cdf.read(sf)
    items = []
    for fname, o, s in files:
        new = sf[o:o + s]
        low = fname.lower()
        for e in edits:
            if e.data is not None and low == e.file.lower():
                new = e.data
            elif e.data is not None and low.endswith(".cdf"):
                if any(n.lower() == e.file.lower() for n, _, _ in cdf.read(new)[2]):
                    new = cdf.replace(new, e.file, e.data)
                    log("  %s/%s" % (fname, e.file))
            elif (e.texs or e.pals or e.formats) and low.endswith(".txd"):
                try:
                    t = txdmod.TXD(new)
                    tr, pr = t.dict_range(e.dict)
                except Exception:
                    continue
                for k, (w, is4) in e.formats.items():
                    tex = t.textures[tr.start + k]
                    vram_old = tex.width // 2 if tex.is4 else tex.width
                    vram_new = w // 2 if is4 else w
                    if vram_old != vram_new:
                        raise ValueError("%s: %s texture %d: VRAM width %d bytes, not %d" % (fname, e.dict, k, vram_old, vram_new))
                    tex.wfield = (tex.wfield & 0xFF00) | (w & 0xFF)
                    tex.info = (tex.info & 0x7FFFFFFF) | (0x80000000 if is4 else 0)
                for k, rows in e.texs.items():
                    tex = t.textures[tr.start + k]
                    if (len(rows[0]), len(rows)) != (tex.width, tex.height):
                        raise ValueError("%s: %s texture %d size differs" % (fname, e.dict, k))
                    t.set_pixels(tr.start + k, rows)
                for p, colors in e.pals.items():
                    if len(t.palettes[pr.start + p].colors) != len(colors):
                        raise ValueError("%s: %s palette %d size differs" % (fname, e.dict, p))
                    t.palettes[pr.start + p].colors = list(colors)
                new = t.build()
                log("  %s: %s textures %s, palettes %s" % (fname, e.dict, sorted(e.texs), sorted(e.pals)))
        items.append((fname, new))
    return cdf.build(name, items)
