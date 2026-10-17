"""Level entities (release EXE SLUS_008.24): enemies, patrol scripts, pickups, player start, lights.

EXE tables
  LEVELS   0x800ABBB8  18 x (descriptor address, overlay name address); overlays load at 0x80122600
  MODELS   0x8009D930  24-byte entries, +0 name pointer (index 1 = jaxsk.skn)
  ENEMIES  0x800AB258  30-byte entries, +0 model index, +4 hit points  (type 0 = the player)
  ITEMS    0x8009F010  32-byte entries, +0 model index; name/description pointers 8 bytes before the entry

Level descriptor (inside the overlay)
  +00 name   +04 TXD   +08 BSP count   +0C BSP name list   +18 init code (MIPS, the level's logic)
  +20 -> {areas, 0x8009E224 handler table, ...}; areas: one 12-byte entry per BSP:
         count u16, ? u16, ? u16, ? u16, records ptr; record = 20 bytes:
         +0 id u16, +2 ?, +3 spawn mode, +4..9 ?, +A ?, +B enemy type, +C x, y, z (int16, world/256),
         +12 script index, +13 ?
  +28 -> scripts: (flag, pointer) pairs; bytecode of u16 op, u16 arg:
         1 start, 2 ?, 4 wait arg frames, 5 ? (8 bytes), 8 call fn (8), 9 jump ptr (8, ends a script),
         17 move to x, y, z (16), 18 call fn at x, y, z (20)
  +30    model preload list (int16, -1)
  +48    player start x, y, z (int32)   +5C start angle (4096 = 360 degrees)
  +74 -> pickups: 24 bytes: item code, arg (int16), x, y, z (int32), 2 x int32; ends with code 0x1B39
  +7C    level archive (.cdf)
BSP lights: tail table of 24-byte entries: x, y, z (int32), colour 0x00BBGGRR, 4 x int16.
"""
import struct

OVL_BASE = 0x80122600
OVL_LIMIT = 0x80127674
LEVELS, NLEVELS = 0x800ABBB8, 18
MODELS, NMODELS = 0x8009D930, 95
ENEMIES, NENEMIES = 0x800AB258, 46
ITEMS, NITEMS = 0x8009F010, 39
OPLEN = {1: 4, 2: 4, 4: 4, 5: 8, 8: 8, 9: 8, 17: 16, 18: 20}
END_PICKUPS = 0x1B39

class Exe:
    def __init__(self, data):
        self.d = data

    def off(self, a):
        return a - 0x80010000 + 0x800

    def u32(self, a):
        return struct.unpack_from("<I", self.d, self.off(a))[0]

    def u16(self, a):
        return struct.unpack_from("<H", self.d, self.off(a))[0]

    def cstr(self, a):
        o = self.off(a)
        return self.d[o:self.d.index(b"\0", o)].decode("latin-1")

    def models(self):
        return [self.cstr(self.u32(MODELS + i * 24)) for i in range(NMODELS)]

    def enemy_types(self):
        m = self.models()
        return [{"model": m[self.u16(ENEMIES + t * 30)], "hp": self.u16(ENEMIES + t * 30 + 4)} for t in range(NENEMIES)]

    def item_types(self):
        m = self.models()
        out = []
        for c in range(NITEMS):
            name = ""
            try:
                name = self.cstr(self.u32(ITEMS + c * 32 - 8))
            except Exception:
                pass
            out.append({"model": m[self.u16(ITEMS + c * 32)], "name": name})
        return out

    def levels(self):
        """[(index, overlay file name, descriptor address)]"""
        return [(i, self.cstr(self.u32(LEVELS + i * 8 + 4)), self.u32(LEVELS + i * 8)) for i in range(NLEVELS)]

class Overlay:
    """A level overlay and its descriptor; edits go to self.data (bytearray)."""
    def __init__(self, name, data, desc):
        self.name, self.data, self.desc = name, bytearray(data), desc

    def a2o(self, a):
        return a - OVL_BASE

    def u32(self, a):
        return struct.unpack_from("<I", self.data, self.a2o(a))[0]

    def i32(self, a):
        return struct.unpack_from("<i", self.data, self.a2o(a))[0]

    def field(self, off):
        return self.u32(self.desc + off)

    def inside(self, a):
        return OVL_BASE <= a < OVL_BASE + len(self.data)

    def cstr(self, a):
        o = self.a2o(a)
        return bytes(self.data[o:self.data.index(b"\0", o)]).decode("latin-1")

    def bsps(self):
        n, lst = self.field(0x08), self.field(0x0C)
        return [self.cstr(self.u32(lst + i * 4)) for i in range(n)]

    # --- player start --------------------------------------------------
    def start(self):
        x, y, z = (self.i32(self.desc + o) for o in (0x48, 0x4C, 0x50))
        return (x, y, z), self.i32(self.desc + 0x5C)

    def set_start(self, pos, angle):
        for o, v in zip((0x48, 0x4C, 0x50, 0x5C), list(pos) + [angle]):
            struct.pack_into("<i", self.data, self.a2o(self.desc + o), int(v))

    # --- enemies -------------------------------------------------------
    def area_entry(self, area):
        s = self.field(0x20)
        if not s:
            return None
        return self.u32(s) + area * 12

    def enemies(self, area):
        e = self.area_entry(area)
        if e is None:
            return []
        count = struct.unpack_from("<H", self.data, self.a2o(e))[0]
        recs = self.u32(e + 8)
        out = []
        for i in range(count):
            o = self.a2o(recs + i * 20)
            r = self.data[o:o + 20]
            x, y, z = struct.unpack_from("<3h", r, 0xC)
            out.append({"index": i, "offset": o, "mode": r[3], "type": r[0xB], "script": r[0x12],
                        "pos": (x * 256, y * 256, z * 256), "raw": bytes(r).hex()})
        return out

    def set_enemy(self, area, index, pos=None, type_=None, script=None, mode=None):
        e = self.area_entry(area)
        o = self.a2o(self.u32(e + 8) + index * 20)
        if pos is not None:
            struct.pack_into("<3h", self.data, o + 0xC, *(int(round(c / 256.0)) for c in pos))
        if type_ is not None:
            self.data[o + 0xB] = int(type_)
        if script is not None:
            self.data[o + 0x12] = int(script)
        if mode is not None:
            self.data[o + 3] = int(mode)

    # --- scripts -------------------------------------------------------
    def scripts(self):
        t = self.field(0x28)
        out = []
        if not t:
            return out
        a = t
        while True:
            flag, p = struct.unpack_from("<iI", self.data, self.a2o(a))
            if not self.inside(p):
                break
            out.append(self.decode(p))
            a += 8
        return out

    def decode(self, p):
        """[(address, op, arg, operands...)] up to the closing jump."""
        ops = []
        for _ in range(1000):
            op, arg = struct.unpack_from("<HH", self.data, self.a2o(p))
            if op not in OPLEN:
                ops.append((p, op, arg, "unknown"))
                break
            body = self.data[self.a2o(p) + 4:self.a2o(p) + OPLEN[op]]
            if op == 17:
                ops.append((p, op, arg, struct.unpack("<3i", body)))
            elif op == 18:
                ops.append((p, op, arg, struct.unpack("<I3i", body)))
            elif OPLEN[op] == 8:
                ops.append((p, op, arg, struct.unpack("<I", body)))
            else:
                ops.append((p, op, arg))
            if op == 9:
                break
            p += OPLEN[op]
        return ops

    def script_points(self, ops):
        """[(address of x, (x, y, z))] for move-to and positioned calls."""
        pts = []
        for o in ops:
            if o[1] == 17:
                pts.append((o[0] + 4, o[3]))
            elif o[1] == 18:
                pts.append((o[0] + 8, o[3][1:]))
        return pts

    def set_point(self, addr, pos):
        struct.pack_into("<3i", self.data, self.a2o(addr), *(int(round(c)) for c in pos))

    # --- pickups -------------------------------------------------------
    def pickups(self):
        a = self.field(0x74)
        out = []
        if not a:
            return out
        for i in range(500):
            code = struct.unpack_from("<h", self.data, self.a2o(a + i * 24))[0]
            if code == END_PICKUPS:
                break
            c, arg, x, y, z = struct.unpack_from("<hh3i", self.data, self.a2o(a + i * 24))
            out.append({"index": i, "code": c, "arg": arg, "pos": (x, y, z)})
        return out

    def set_pickup(self, index, pos=None, code=None, arg=None):
        o = self.a2o(self.field(0x74) + index * 24)
        if code is not None:
            struct.pack_into("<h", self.data, o, int(code))
        if arg is not None:
            struct.pack_into("<h", self.data, o + 2, int(arg))
        if pos is not None:
            struct.pack_into("<3i", self.data, o + 4, *(int(round(c)) for c in pos))

def find_overlay(exe, sf_get, bsp_file):
    """(Overlay, area index) of the level that uses bsp_file, or (None, None).
    sf_get(name) returns a file from SF.CDF."""
    for i, ovl, desc in exe.levels():
        try:
            o = Overlay(ovl, sf_get(ovl), desc)
            names = [n.lower() for n in o.bsps()]
        except Exception:
            continue
        if bsp_file.lower() in names:
            return o, names.index(bsp_file.lower())
    return None, None

# --- BSP lights ----------------------------------------------------------

def light_table(b):
    """(offset in b.tail of the light count, count) for a parsed BSP."""
    sizes = (44, 84, 44, 84) if b.proto else (48, 84, 48, 84)
    o = 0
    for s in sizes:
        n = struct.unpack_from("<i", b.tail, o)[0]
        o += 4 + n * s
    return o, struct.unpack_from("<i", b.tail, o)[0]

def lights(b):
    o, n = light_table(b)
    out = []
    for i in range(n):
        x, y, z, col, p0, p1, p2, p3 = struct.unpack_from("<3iI4h", b.tail, o + 4 + i * 24)
        out.append({"index": i, "pos": (x, y, z), "color": col, "params": (p0, p1, p2, p3)})
    return out

def set_light(b, index, pos=None, color=None, params=None):
    o, n = light_table(b)
    tail = bytearray(b.tail)
    e = o + 4 + index * 24
    if pos is not None:
        struct.pack_into("<3i", tail, e, *(int(round(c)) for c in pos))
    if color is not None:
        struct.pack_into("<I", tail, e + 12, int(color) & 0xFFFFFF)
    if params is not None:
        struct.pack_into("<4h", tail, e + 16, *(int(v) for v in params))
    b.tail = bytes(tail)
