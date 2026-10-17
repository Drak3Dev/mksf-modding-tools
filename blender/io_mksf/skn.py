"""MKSF character model (.skn).

Header (64 bytes, int32): magic 0xAABBCCDD, name[16], numGroups, numFaces, numTex, numPal, numPal,
                          numGroups, 0, numVerts, 0, numFaces, 0   (the zeros are filled at runtime)
Vertices  x, y, z, pad (int16)            -- relative to the owning group's pivot; PS1 axes: y points down
Faces     v[4] (int16), tex, pal (int16), uv[4] (u8 pairs)
          vertices go around the polygon; a triangle repeats a vertex (normally v[3] == v[2]).
          tex/pal are indices inside the TXD dictionary named like the model (e.g. jaxsk.tex).
Groups    pivot x, y, z, vertCount, vertStart, pad (int16), name[24]   -- body parts, pivots are absolute
"""
import struct

MAGIC = 0xAABBCCDD

class SKN:
    def __init__(self, data=None):
        self.name = b""
        self.ntex = self.npal = 0
        self.verts = []    # [x, y, z, pad]
        self.faces = []    # [v0, v1, v2, v3, tex, pal, (u0, v0, ... u3, v3)]
        self.groups = []   # [x, y, z, count, start, pad, name(bytes 24)]
        if data is not None:
            self.parse(data)

    def parse(self, d):
        h = struct.unpack_from("<I16s11i", d)
        assert h[0] == MAGIC, "not an SKN"
        self.name = h[1]
        ng, nf, self.ntex, self.npal, npal2, ng2, z1, nv, z2, nf2, z3 = h[2:]
        assert ng == ng2 and nf == nf2 and self.npal == npal2
        o = 64
        self.verts = [list(struct.unpack_from("<4h", d, o + i * 8)) for i in range(nv)]
        o += nv * 8
        self.faces = []
        for i in range(nf):
            f = struct.unpack_from("<6h8B", d, o + i * 20)
            self.faces.append(list(f[:6]) + [tuple(f[6:])])
        o += nf * 20
        self.groups = [list(struct.unpack_from("<6h", d, o + i * 36)) + [d[o + i * 36 + 12:o + i * 36 + 36]]
                       for i in range(ng)]
        assert o + ng * 36 == len(d)

    def build(self):
        ng, nf, nv = len(self.groups), len(self.faces), len(self.verts)
        out = bytearray(struct.pack("<I16s11i", MAGIC, self.name, ng, nf, self.ntex, self.npal, self.npal,
                                    ng, 0, nv, 0, nf, 0))
        for v in self.verts:
            out += struct.pack("<4h", *v)
        for f in self.faces:
            out += struct.pack("<6h8B", *(f[:6] + list(f[6])))
        for g in self.groups:
            out += struct.pack("<6h", *g[:6]) + g[6]
        return bytes(out)

    def same(self, other):
        """Equal apart from the unused 4th UV of triangles."""
        if self.verts != other.verts or self.groups != other.groups or len(self.faces) != len(other.faces):
            return False
        for a, b in zip(self.faces, other.faces):
            n = 6 if a[3] == a[2] else 8
            if a[:6] != b[:6] or a[6][:n] != b[6][:n]:
                return False
        return True

    def group_name(self, gi):
        return self.groups[gi][6].split(b"\0")[0].decode("latin-1")

    def vertex_group(self):
        """Group index of every vertex."""
        owner = [None] * len(self.verts)
        for gi, g in enumerate(self.groups):
            for i in range(g[4], g[4] + g[3]):
                owner[i] = gi
        return owner

    def world(self, i, owner=None):
        g = self.groups[(owner or self.vertex_group())[i]]
        v = self.verts[i]
        return (v[0] + g[0], v[1] + g[1], v[2] + g[2])
