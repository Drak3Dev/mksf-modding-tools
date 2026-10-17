"""MKSF level geometry (.bsp). Layout from the loader FUN_80026c60 (release EXE).

  name[16], nodeCount
  nodes      nodeCount x 36
  polyCount  then polygons, 56 bytes each:
      4 x (x, y, z, w) int16      vertex records r0..r3 (y points down)
      pal u16, uv0 u16            pal: palette index in the level's dictionary (<name>.tex)
      tex u16, uv1 u16            tex: texture index in the dictionary
      4 x colour u32              RGB + GPU code (0x3C = gouraud textured quad)
    PS1 corners (c3 opposite c0): c0 = r0, c1 = r1, c2 = r3, c3 = r2
    UVs (u = low byte, v = high byte, relative to the texture): c0 = uv0, c1 = uv1, c2 = r1.w, c3 = r0.w
    r2.w and r3.w are something else (kept as is).
  then collision / zone tables (kept as raw bytes): count + 48-byte entries, count + 84-byte groups,
  the same again, count + 24-byte entries, count + 156-byte zones (zone[0] = polygon count,
  zone[1] = first polygon; the game uses zone 0's first and the counts after it), then short arrays.
  The tail never refers to polygon numbers except through the zones.

Prototype (older format, loader FUN_80024xxx in the prototype PSX.EXE): same header and nodes, then
  polyCount and polygons of 40 bytes: 4 x (x, y, z, w) records, then uv3 u16, uv2 u16, flags u32.
    tex = r0.w, pal = r1.w, uv0 = r2.w, uv1 = r3.w (same corner order and UV slots as the release)
    flags bit 31: gouraud, low 16 bits = entry in the colour table (4 x BGR555, one per record r0..r3)
          otherwise: flat colour, BGR555 in the low 15 bits
          bit 30: semi-transparent (also drawn double-sided), bit 28: untextured
  colour table: count + 8-byte entries; then the tail as in the release, but collision entries are 44 bytes.

Node (36 bytes): count, start, 0, 0, child1, child2, leaf number, ?, pad (4), sphere x, y, z, r,
  plane nx, ny, nz, d (int16, n in 1/4096). Only leaves (children -1) own polygons: [start, start+count).
  Distance = (n . p) / 4096 + d; child1 is the negative side, child2 the positive side (and the plane).
  Polygons of all leaves come first, in leaf order; zone polygons follow.
"""
import math
import struct

NODE = "<4h2h2h2h4h4h"

class Node:
    def __init__(self, raw):
        v = struct.unpack(NODE, raw)
        self.count, self.start, self.z0, self.z1 = v[0:4]
        self.c1, self.c2, self.leaf, self.x = v[4:8]
        self.pad = v[8:10]
        self.sphere = list(v[10:14])
        self.plane = v[14:18]

    def pack(self):
        return struct.pack(NODE, self.count, self.start, self.z0, self.z1, self.c1, self.c2, self.leaf, self.x,
                           *self.pad, *self.sphere, *self.plane)

    def is_leaf(self):
        return self.c1 == -1

class Poly:
    __slots__ = ("r", "pal", "uv0", "tex", "uv1", "colors")

    def corners(self):
        """[(x, y, z, u, v, color)] for c0..c3 in PS1 order."""
        r = self.r
        uv = [self.uv0, self.uv1, r[1][3] & 0xFFFF, r[0][3] & 0xFFFF]
        recs = [r[0], r[1], r[3], r[2]]
        cols = self.colors
        return [(recs[k][0], recs[k][1], recs[k][2], uv[k] & 0xFF, uv[k] >> 8, cols[k]) for k in range(4)]

def bgr555_rgb(c):
    """BGR555 (as the prototype stores colours, 16 = neutral) -> 0xBBGGRR at the release's scale (128 = neutral)."""
    return ((c & 31) << 3) | (((c >> 5) & 31) << 11) | (((c >> 10) & 31) << 19)

class ProtoPoly:
    __slots__ = ("r", "uv3", "uv2", "flags", "table")

    @property
    def tex(self):
        return self.r[0][3]

    @property
    def pal(self):
        return self.r[1][3]

    @property
    def semi(self):
        return bool(self.flags & 0x40000000)

    @property
    def textured(self):
        return not self.flags & 0x10000000

    @property
    def colors(self):
        """Release-style colours (RGB, code in the top byte) of c0..c3."""
        code = (0x3E if self.semi else 0x3C) << 24
        if self.flags & 0x80000000:
            e = self.table[self.flags & 0xFFFF] if (self.flags & 0xFFFF) < len(self.table) else (0, 0, 0, 0)
            recs = [e[0], e[1], e[3], e[2]]
        else:
            recs = [self.flags & 0x7FFF] * 4
        return [bgr555_rgb(c) | code for c in recs]

    @property
    def wall(self):
        return bool(self.flags & 0x10000000)

    def corners(self):
        r = self.r
        cols = self.colors
        if self.wall:
            # Compressed vertical wall: top edge r0 -> r1, r2 = (?, height, pal, pal), r3 = 4 UVs.
            h = r[2][1]
            recs = [r[0][:3], r[1][:3], [r[0][0], r[0][1] + h, r[0][2]], [r[1][0], r[1][1] + h, r[1][2]]]
            uv = [x & 0xFFFF for x in r[3]]
        else:
            uv = [r[2][3] & 0xFFFF, r[3][3] & 0xFFFF, self.uv2, self.uv3]
            recs = [r[0], r[1], r[3], r[2]]
        return [(recs[k][0], recs[k][1], recs[k][2], uv[k] & 0xFF, uv[k] >> 8, cols[k]) for k in range(4)]

def _tail_ok(t, sizes):
    o = 0
    try:
        for size in sizes:
            n = struct.unpack_from("<i", t, o)[0]
            if n < 0 or n > 100000:
                return False
            o += 4 + n * size
        n = struct.unpack_from("<i", t, o)[0]
        return 0 <= n < 1000 and o + 4 + n * 156 <= len(t)
    except struct.error:
        return False

RELEASE_TAIL = (48, 84, 48, 84, 24)
PROTO_TAIL = (44, 84, 44, 84, 24)

class BSP:
    def __init__(self, data):
        self.name = data[:16]
        n = struct.unpack_from("<i", data, 16)[0]
        o = 20
        self.nodes = data[o:o + n * 36]
        o += n * 36
        p = struct.unpack_from("<i", data, o)[0]
        o += 4
        self.poly_offset = o
        self.polys = []
        end = o + p * 56
        self.proto = not (end <= len(data) and _tail_ok(data[end:], RELEASE_TAIL))
        if not self.proto:
            for i in range(p):
                v = struct.unpack_from("<16h2H2H4I", data, o + i * 56)
                q = Poly()
                q.r = [list(v[k * 4:k * 4 + 4]) for k in range(4)]
                q.pal, q.uv0, q.tex, q.uv1 = v[16:20]
                q.colors = list(v[20:24])
                self.polys.append(q)
            self.table = []
            self.tail = data[end:]
        else:
            end = o + p * 40
            nt = struct.unpack_from("<i", data, end)[0]
            self.table = [struct.unpack_from("<4H", data, end + 4 + i * 8) for i in range(nt)]
            for i in range(p):
                v = struct.unpack_from("<16h2HI", data, o + i * 40)
                q = ProtoPoly()
                q.r = [list(v[k * 4:k * 4 + 4]) for k in range(4)]
                q.uv3, q.uv2, q.flags = v[16:19]
                q.table = self.table
                self.polys.append(q)
            self.tail = data[end + 4 + nt * 8:]
            if not _tail_ok(self.tail, PROTO_TAIL):
                raise ValueError("unknown .bsp layout")
        self.zones = self._zones()

    def _zone_offset(self):
        t, o = self.tail, 0
        for size in (PROTO_TAIL if self.proto else RELEASE_TAIL):
            n = struct.unpack_from("<i", t, o)[0]
            o += 4 + n * size
        return o

    def _zones(self):
        o = self._zone_offset()
        n = struct.unpack_from("<i", self.tail, o)[0]
        return [struct.unpack_from("<2i", self.tail, o + 4 + i * 156) for i in range(n)]   # (count, first)

    # ---------------------------------------
    # Tree
    # ---------------------------------------

    def node_list(self):
        return [Node(self.nodes[i:i + 36]) for i in range(0, len(self.nodes), 36)]

    def set_nodes(self, nodes):
        self.nodes = b"".join(n.pack() for n in nodes)

    @staticmethod
    def side(node, p):
        nx, ny, nz, d = node.plane
        return (nx * p[0] + ny * p[1] + nz * p[2]) / 4096.0 + d

    def leaf_path(self, p, nodes=None):
        """Node indices from the root to the leaf containing point p (game coordinates)."""
        nodes = nodes or self.node_list()
        i, path = 0, []
        while True:
            path.append(i)
            n = nodes[i]
            if n.is_leaf():
                return path
            i = n.c1 if self.side(n, p) < 0 else n.c2

    def leaf_of_poly(self, nodes=None):
        """Leaf node index of every polygon (None for zone polygons)."""
        nodes = nodes or self.node_list()
        owner = [None] * len(self.polys)
        for i, n in enumerate(nodes):
            if n.is_leaf():
                for k in range(n.start, n.start + n.count):
                    owner[k] = i
        return owner

    def set_polys(self, leaf_polys, zone_polys, nodes=None, grow=()):
        """Rebuild the polygon array from {leaf node index: [Poly]} and the zone polygons; fixes leaf
        ranges and the zone start. Spheres of the leaves of polygons in grow (and their ancestors)
        are enlarged to enclose them (the original spheres are approximate, so only new ones count)."""
        grow = set(id(q) for q in grow)
        nodes = nodes or self.node_list()
        leaves = sorted((i for i, n in enumerate(nodes) if n.is_leaf()), key=lambda i: (nodes[i].start, i))
        polys = []
        for i in leaves:
            ps = leaf_polys.get(i, [])
            nodes[i].start, nodes[i].count = len(polys), len(ps)
            polys += ps
        if len(polys) > 32767:
            raise ValueError("too many polygons for 16-bit leaf ranges")
        zone_first = len(polys)
        polys += zone_polys
        # Spheres: every node must enclose all polygons below it.
        parent = {}
        for i, n in enumerate(nodes):
            if not n.is_leaf():
                parent[n.c1] = i
                parent[n.c2] = i
        for i in leaves:
            for q in leaf_polys.get(i, []):
                if id(q) not in grow:
                    # original spheres are a little loose; only polygons outside them need anything
                    sx, sy, sz, sr = nodes[i].sphere
                    if all((r[0] - sx) ** 2 + (r[1] - sy) ** 2 + (r[2] - sz) ** 2 <= (sr + 1) ** 2 for r in q.r):
                        continue
                for r in q.r:
                    j = i
                    while j is not None:
                        s = nodes[j].sphere
                        dist = math.sqrt((r[0] - s[0]) ** 2 + (r[1] - s[1]) ** 2 + (r[2] - s[2]) ** 2)
                        if dist > s[3]:
                            s[3] = min(32767, int(math.ceil(dist)) + 1)
                        j = parent.get(j)
        self.polys = polys
        self.set_nodes(nodes)
        o = self._zone_offset()
        n = struct.unpack_from("<i", self.tail, o)[0]
        tail = bytearray(self.tail)
        for z in range(n):
            struct.pack_into("<i", tail, o + 4 + z * 156 + 4, zone_first)
        self.tail = bytes(tail)
        self.zones = self._zones()

    def build(self):
        out = bytearray(self.name)
        out += struct.pack("<i", len(self.nodes) // 36) + self.nodes
        out += struct.pack("<i", len(self.polys))
        if self.proto:
            for q in self.polys:
                out += struct.pack("<16h2HI", *(sum(q.r, []) + [q.uv3, q.uv2, q.flags]))
            out += struct.pack("<i", len(self.table)) + b"".join(struct.pack("<4H", *e) for e in self.table)
        else:
            for q in self.polys:
                out += struct.pack("<16h2H2H4I", *(sum(q.r, []) + [q.pal, q.uv0, q.tex, q.uv1] + q.colors))
        return bytes(out + self.tail)

    def dict_name(self):
        return self.name.split(b"\0")[0].decode("latin-1")

# ---------------------------------------------------------------------------
# Collision (release). Tail: C1 walls (48 b), G1 groups (84 b), C2 floors (48 b), G2 groups (84 b),
# lights (24 b), zones (156 b), then int M and per-leaf tables: arr0, arr1, arr2 (M int16 offsets,
# indexed by the node's leaf number) into list0 (G1 groups), list1 (G2 groups), list2 (lights); a leaf's
# block there is [count, index, index, ...]. Each list = int length + int16 entries (lengths stay even so
# the next int stays aligned), then the rest. Lookup: FUN_80028e74 (leaf block of a point).
# Quad: 4 x (x, y, z, w), plane nx, ny, nz, d (n in 1/4096, solid on the positive side), a, flags, b, c.
#   flags 0x8000 | axis bit << 12 for axis-aligned quads (x 0x4, y 0x2, z 0x1), w = 2D bounding box in the
#   two other axes (min a, min b, max a, max b); slanted quads: flags 0x8000, w = (axis code 1/2/4, 0, 0, 0).
# A group's quads follow the previous groups' quads; walls a, b, c = 0, 0, 209; floors 0, 0, 0.
# ---------------------------------------------------------------------------

def _tail_layout(t, proto=False):
    o, lay = 0, {}
    for name, size in (("C1", 44 if proto else 48), ("G1", 84), ("C2", 44 if proto else 48), ("G2", 84),
                       ("K", 24), ("Z", 156)):
        n = struct.unpack_from("<i", t, o)[0]
        lay[name] = (o, n, size)
        o += 4 + n * size
    m = struct.unpack_from("<i", t, o)[0]
    lay["M"] = (o, m)
    o += 4
    lay["arr"] = [o + k * 2 * m for k in range(3)]
    o += 3 * 2 * m
    lists = []
    for k in range(3):
        n = struct.unpack_from("<i", t, o)[0]
        lists.append((o, n))
        o += 4 + 2 * n
    lay["lists"] = lists
    lay["rest"] = o
    return lay

def collision_quad(pts, normal, wall):
    """48-byte collision entry. pts: 3 or 4 game-space corners around the polygon; normal: unit vector
    pointing into the solid side."""
    pts = [tuple(int(round(c)) for c in p) for p in pts]
    if len(pts) == 3:
        pts.append(pts[2])
    n = [int(round(c * 4096)) for c in normal]
    d = -int(round(sum(a * b for a, b in zip(n, pts[0])) / 4096.0))
    ax = max(range(3), key=lambda k: abs(normal[k]))
    aligned = abs(normal[ax]) > 0.999
    if aligned:
        n = [0, 0, 0]
        n[ax] = 4096 if normal[ax] > 0 else -4096
        d = -int(round(n[ax] * pts[0][ax] / 4096.0))
        ab = [k for k in range(3) if k != ax]
        w = [min(p[ab[0]] for p in pts), min(p[ab[1]] for p in pts), max(p[ab[0]] for p in pts), max(p[ab[1]] for p in pts)]
        flags = 0x8000 | ({0: 4, 1: 2, 2: 1}[ax] << 12)
    else:
        w = [{0: 1, 1: 2, 2: 4}[ax], 0, 0, 0]
        flags = 0x8000
    tail = (0, flags - 0x10000, 0, 209) if wall else (0, flags - 0x10000, 0, 0)
    return struct.pack("<16h4h4h", *sum(([*p, wi] for p, wi in zip(pts, w)), []), *n, d, *tail)

def _group(template, count, gid):
    g = bytearray(template)
    struct.pack_into("<i", g, 80, gid)
    struct.pack_into("<9h", g, 0, 4096, 0, 0, 0, 4096, 0, 0, 0, 4096)
    struct.pack_into("<3h3i", g, 18, 0, 0, 0, 0, 0, 0)
    struct.pack_into("<iI", g, 40, count, 0)
    return bytes(g)

def add_collision(b, walls, floors, nodes=None):
    """Append collision quads (bytes from collision_quad) as one new G1 group (walls) and one new G2 group
    (floors). walls/floors: [(entry bytes, set of leaf numbers)]."""
    if b.proto:
        raise ValueError("collision of prototype levels is not supported")
    t = bytearray(b.tail)
    lay = _tail_layout(bytes(t))
    out = bytearray()
    # rebuild C1, G1, C2, G2 with the additions
    pieces = []
    new_group = {}
    ids = [struct.unpack_from("<i", t, lay[g][0] + 4 + i * 84 + 80)[0] for g in ("G1", "G2") for i in range(lay[g][1])]
    next_id = max(ids + [0]) + 1
    for cname, gname, items, key in (("C1", "G1", walls, 0), ("C2", "G2", floors, 1)):
        co, cn, cs = lay[cname]
        go, gn, gs = lay[gname]
        cdata = t[co + 4:co + 4 + cn * cs] + b"".join(e for e, _ in items)
        gdata = t[go + 4:go + 4 + gn * gs]
        if items:
            tmpl = gdata[:gs] if gn else bytes([0] * 36) + struct.pack("<i", -65536) + bytes(8) + b"\xff" * 36
            gdata += _group(tmpl, len(items), next_id)
            next_id += 1
            new_group[key] = gn
        pieces.append(struct.pack("<i", cn + len(items)) + cdata)
        pieces.append(struct.pack("<i", gn + (1 if items else 0)) + gdata)
    out += b"".join(pieces)
    ko = lay["K"][0]
    mo, m = lay["M"]
    out += t[ko:mo + 4]                      # lights, zones, M
    arrs = [list(struct.unpack_from("<%dh" % m, t, a)) for a in lay["arr"]]
    lists = [list(struct.unpack_from("<%dh" % n, t, o + 4)) for o, n in lay["lists"]]
    for key, items in ((0, walls), (1, floors)):
        if key not in new_group:
            continue
        leaves = set()
        for _, ls in items:
            leaves |= ls
        nleaf = sum(1 for x in (nodes or b.node_list()) if x.is_leaf())
        per_leaf = []                          # each leaf: [count, group, group, ...]
        for leaf in range(nleaf):
            lo = arrs[key][leaf]
            hi = arrs[key][leaf + 1] if leaf + 1 < nleaf else len(lists[key])
            blk = list(lists[key][lo:hi])
            if leaf in leaves:
                blk = [blk[0] + 1] + blk[1:blk[0] + 1] + [new_group[key]] + blk[blk[0] + 1:]
            per_leaf.append(blk)
        if sum(len(x) for x in per_leaf) % 2:
            for blk in per_leaf:
                if new_group[key] in blk[1:blk[0] + 1]:
                    blk[:] = [blk[0] + 1] + blk[1:blk[0] + 1] + [new_group[key]] + blk[blk[0] + 1:]
                    break
        offs, flat = [], []
        for x in per_leaf:
            offs.append(len(flat))
            flat += x
        arrs[key] = offs + arrs[key][len(offs):]
        lists[key] = flat
    for a in arrs:
        out += struct.pack("<%dh" % m, *a)
    for l in lists:
        out += struct.pack("<i%dh" % len(l), len(l), *l)
    out += t[lay["rest"]:]
    b.tail = bytes(out)
    b.zones = b._zones()
