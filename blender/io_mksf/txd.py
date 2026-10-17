"""MKSF texture dictionary (.txd).

Layout:
  header   numTex, numPal, numDicts                       (3 x int32)
  dicts    name[16], firstTex i16, firstPal i16             (20 bytes each)
  textures info u32, wfield u16, height u16, idx u16, src i16, then pixel data if src < 0, padded to 4
           info: bits 0-9 VRAM X (bytes), 10-19 Y, 20-27 area, bit 31 = 4bpp
           wfield & 0xFF = width (0 means 256); src >= 0: pixels are shared with texture #src
  palettes count i16, idx i16 (index within dict), -1 i32, count x u16 (PS1 BGR555)
  trailer  4 zero bytes

A texture's palette is not stored here: each SKN face carries (texture, palette) indices within the dict.

  python3 txd.py list FILE.txd
  python3 txd.py export FILE.txd DICT OUTDIR [SKN]   -> PNG per (texture, palette) pair
"""
import os
import struct
import sys

class Texture:
    def __init__(self, info, wfield, height, idx, src, data):
        self.info, self.wfield, self.height, self.idx, self.src, self.data = info, wfield, height, idx, src, data
        self.pad = b""

    @property
    def is4(self):
        return bool(self.info >> 31)

    @property
    def width(self):
        return (self.wfield & 0xFF) or 256

    def data_size(self):
        return self.width * self.height // 2 if self.is4 else self.width * self.height

class Palette:
    def __init__(self, idx, pad, colors):
        self.idx, self.pad, self.colors = idx, pad, colors

class Dict:
    def __init__(self, name, first_tex, first_pal):
        self.name, self.first_tex, self.first_pal = name, first_tex, first_pal

class TXD:
    def __init__(self, data):
        nt, npal, nd = struct.unpack_from("<3i", data)
        o = 12
        self.dicts = []
        for i in range(nd):
            n, ft, fp = struct.unpack_from("<16shh", data, o)
            self.dicts.append(Dict(n.split(b"\0")[0].decode("latin-1"), ft, fp))
            o += 20
        self.textures = []
        for i in range(nt):
            info, wf, h, idx, src = struct.unpack_from("<IHHHh", data, o)
            o += 12
            t = Texture(info, wf, h, idx, src, None)
            if src < 0:
                n = t.data_size()
                t.data = bytearray(data[o:o + n])
                o += n
                t.pad = data[o:(o + 3) & ~3]   # garbage, kept for byte-exact rebuilds
                o = (o + 3) & ~3
            self.textures.append(t)
        self.palettes = []
        for i in range(npal):
            n, idx, pad = struct.unpack_from("<hhi", data, o)
            o += 8
            self.palettes.append(Palette(idx, pad, list(struct.unpack_from("<%dH" % n, data, o))))
            o += n * 2
        self.trailer = data[o:]

    def build(self):
        out = bytearray(struct.pack("<3i", len(self.textures), len(self.palettes), len(self.dicts)))
        for d in self.dicts:
            out += d.name.encode("latin-1").ljust(16, b"\0") + struct.pack("<hh", d.first_tex, d.first_pal)
        for t in self.textures:
            out += struct.pack("<IHHHh", t.info, t.wfield, t.height, t.idx, t.src)
            if t.src < 0:
                assert len(t.data) == t.data_size()
                out += t.data
                n = -len(out) % 4
                out += t.pad if len(t.pad) == n else b"\0" * n
        for p in self.palettes:
            out += struct.pack("<hhi", len(p.colors), p.idx, p.pad) + struct.pack("<%dH" % len(p.colors), *p.colors)
        out += self.trailer
        return bytes(out)

    def dict_range(self, name):
        """(textures, palettes) index ranges of a dictionary (name with or without .tex)."""
        for i, d in enumerate(self.dicts):
            if d.name.lower() in (name.lower(), name.lower() + ".tex"):
                nxt = self.dicts[i + 1] if i + 1 < len(self.dicts) else Dict("", len(self.textures), len(self.palettes))
                return range(d.first_tex, nxt.first_tex), range(d.first_pal, nxt.first_pal)
        raise KeyError(name)

    def pixels(self, ti):
        """Palette indices of texture ti as a list of rows."""
        t = self.textures[ti]
        src = self.textures[t.src] if t.src >= 0 else t
        w, h = t.width, t.height
        if t.is4:
            flat = []
            for b in src.data:
                flat += (b & 15, b >> 4)
        else:
            flat = list(src.data)
        return [flat[y * w:(y + 1) * w] for y in range(h)]

    def set_pixels(self, ti, rows):
        t = self.textures[ti]
        if t.src >= 0:
            t = self.textures[t.src]
        if t.is4:
            flat = [i for r in rows for i in r]
            t.data = bytearray(flat[i] | (flat[i + 1] << 4) for i in range(0, len(flat), 2))
        else:
            t.data = bytearray(i for r in rows for i in r)

def bgr555_to_rgba(c):
    r, g, b = (c & 31) << 3, ((c >> 5) & 31) << 3, ((c >> 10) & 31) << 3
    a = 0 if c == 0 else 255   # PS1: 0x0000 is transparent
    return (r | r >> 5, g | g >> 5, b | b >> 5, a)

def rgb_to_bgr555(r, g, b, stp=0):
    return (r >> 3) | ((g >> 3) << 5) | ((b >> 3) << 10) | (stp << 15)

if __name__ == "__main__":
    txd = TXD(open(sys.argv[2], "rb").read())
    if sys.argv[1] == "list":
        for i, d in enumerate(txd.dicts):
            tr, pr = txd.dict_range(d.name)
            print("%-14s tex %d..%d pal %d..%d" % (d.name, tr.start, tr.stop - 1, pr.start, pr.stop - 1))
    elif sys.argv[1] == "dump":
        tr, pr = txd.dict_range(sys.argv[3])
        for k, ti in enumerate(tr):
            t = txd.textures[ti]
            print("tex %2d #%d %3dx%-3d %s info=%08x x=%d y=%d area=%d wf=%04x idx=%d src=%d" % (
                k, ti, t.width, t.height, "4bpp" if t.is4 else "8bpp", t.info, t.info & 0x3FF,
                (t.info >> 10) & 0x3FF, (t.info >> 20) & 0xFF, t.wfield, t.idx, t.src))
        for k, pi in enumerate(pr):
            p = txd.palettes[pi]
            print("pal %d #%d colors=%d idx=%d" % (k, pi, len(p.colors), p.idx))
