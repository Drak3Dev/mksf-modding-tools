# Mortal Kombat: Special Forces (PS1) — file formats

Everything here was reverse-engineered for this project from the USA release (`SLUS_008.24`) and checked by
round-tripping every file of both discs byte-for-byte and by testing changes in the game.
All values are little-endian. Addresses are for the release EXE unless noted.

## Disc

* Release: `SF.CDF` at LBA 355, directly followed by other files; `JUNK.PAD` (30 MB of padding) at the end of the disc.
  The game finds `SF.CDF` through the ISO 9660 directory (`CdSearchFile`), so the archive can be moved or grown:
  the tools grow it into the padding right after it and adjust the directory records.
* Sectors are Mode 2 Form 1 (subheader `0x08`, last sector `0x89`).
* Prototype: `SF.CDF` at LBA 113559, `PSX.EXE`, `JUNK.PAD`.

## CDF archive

```
char  name[12]; int32 count; int32 total_size;
count × { char name[12]; int32 offset; int32 size; }
```
* Root `SF.CDF`: data 2048-aligned, every file followed by at least one padding byte.
* Level archives (`<level>.cdf`, loaded into RAM whole): 4-byte alignment, no gap after the table.
* The table is read into a 0x2800-byte buffer (≈ 511 files max). Files are allocated by size (`size + 0x800`).

Character models exist in several copies: the root `SF.CDF` and every level `.cdf` that uses them.

## TXD — texture dictionaries

```
int32 numTex, numPal, numDicts;
numDicts × { char name[16]; int16 firstTex; int16 firstPal; }
numTex   × { uint32 info; uint16 wfield; uint16 height; uint16 idx; int16 src; [pixels if src < 0, padded to 4] }
numPal   × { int16 count; int16 idx; int32 -1; uint16 colours[count]; }   // BGR555
int32 0
```
* `info`: bits 0–9 VRAM X **in bytes**, 10–19 Y, 20–27 area, bit 31 = 4 bpp. `wfield & 0xFF` = width (0 = 256).
  A 4 bpp texture 48 wide occupies the same VRAM bytes as an 8 bpp one 24 wide.
* `src >= 0`: pixels shared with texture `src` (often with another palette).
* Padding bytes are garbage (kept for exact rebuilds).
* **The palette of a texture is not stored here** — each model face / level polygon names its own palette.
* Every level TXD contains the dictionaries of everything used in that level (the player `jaxsk.tex` is in all of
  them, at different VRAM spots). The TXD is loaded into a 530000-byte buffer.

## SKN — character models

```
uint32 0xAABBCCDD; char name[16];
int32 numGroups, numFaces, numTex, numPal, numPal, numGroups, 0, numVerts, 0, numFaces, 0;
numVerts  × { int16 x, y, z, pad; }                       // relative to the group pivot, y down
numFaces  × { int16 v[4], tex, pal; uint8 uv[8]; }       // vertices around the polygon; triangle: v3 == v2
numGroups × { int16 x, y, z, count, start, pad; char name[24]; }   // body parts, absolute pivots
```
* `tex`/`pal` index the dictionary named like the model (`jaxsk.tex`).
* Animations address body parts by index. Jax lists the right forearm before the right bicep; other humanoids
  (e.g. Jarek) the other way round.

## BSP — level geometry (release)

```
char name[16];            // = texture dictionary <name>.tex
int32 numNodes; numNodes × Node (36 bytes)
int32 numPolys; numPolys × Poly (56 bytes)
tail: collision, groups, lights, zones, per-leaf tables (below)
```
**Node**: `int16 count, start, 0, 0, child1, child2, leaf, ?; int16 pad[2]; int16 sphere x, y, z, r;
int16 plane nx, ny, nz, d`. Distance = n·p/4096 + d; child1 is the negative side. Only leaves (children −1) own
polygons, `[start, start+count)`; leaf polygons come first in leaf order, zone polygons follow.
The game culls whole leaves by their bounding spheres — every polygon must lie inside its leaf's sphere and its
ancestors' (the tools grow them).

**Poly**: `4 × {int16 x, y, z, w}`, `uint16 pal, uv0`, `uint16 tex, uv1`, `4 × uint32 colour`
(RGB + GPU code `0x3C` gouraud-textured quad, `0x3E` semi-transparent).
PS1 corners (c3 opposite c0): c0 = r0, c1 = r1, c2 = r3, c3 = r2; UVs: c0 = uv0, c1 = uv1, c2 = r1.w, c3 = r0.w.
At load `tex`/`pal` become tpage/clut and UVs are offset (`FUN_80026c60`, `FUN_80027040`).
Large polygons close to the camera are dropped by the renderer — original geometry is tiled at ~256 units.

**Tail** (in order):
* `C1` walls and `C2` floors — `int32 count` + 48-byte quads:
  `4 × {x, y, z, w}`, `nx, ny, nz, d`, `a, flags, b, c` (int16).
  Axis-aligned: `flags = 0x8000 | axis << 12` (x 4, y 2, z 1), `w` = 2D bounding box in the two other axes
  (min a, min b, max a, max b). Slanted: `flags = 0x8000`, `w = (axis code 1/2/4, 0, 0, 0)`. Tested double-sided.
* Special fields (`a` @+40, `flags` @+42, `b` @+44, `c` @+46; the game reads `a | flags << 16`):
  * `flags & 0x8000` clear: the corners are in the group's local space and go through its matrix and translation
    (`FUN_8002B720`, GTE); `w` of corner 0 is then the projection axis code. The plane is always in world space.
  * walls `a & 2` — **ladder**: the player probes ±40 sideways / 35 ahead, both must hit such a quad; the climb ends
    at the quad's top edge. Any vertical wall ≥ ~100 wide works, the group does not matter.
  * walls `a & 0x80` — pushable box (moves its group and zone); floors `a & 0x80` — box top.
  * `a & 0x100` — part of a door / lift: the group follows its zone at run time.
  * floors: `a & 0xF` surface type, `a & 8` platform, `a & 0x8000` floor of zone `a & 0xFF`; `flags & 8` —
    **camera zone** of the original camera (`b` = camera setting), not solid. The player skips `0x88000`.
  * Climbing onto a ledge (triangle) needs no flag: a wall in front and a floor within reach of both hands.
* `G1`, `G2` — groups of C1 / C2 quads, `int32 count` + 84 bytes: matrix, …, quad count @+40, object id @+80
  (ids unique across G1+G2). A group's quads follow the previous groups' quads.
* Lights — `int32 count` + 24 bytes: `int32 x, y, z; uint32 0x00BBGGRR; int16 intensity, ?, ?, radius`.
* Zones (doors and other moving parts) — `int32 count` + 156 bytes; `[0]` polygon count, `[1]` first polygon
  (the game uses zone 0's first and the counts after it). Zone polygons are in object-local coordinates.
* `int32 M` (leaves, padded to even), then `arr0, arr1, arr2` (M int16 offsets, indexed by the node's `leaf`
  field) into `list0` (G1 groups), `list1` (G2 groups), `list2` (lights); a leaf's block is
  `[count, index, …]`; each list is `int32 length` + int16 entries (lengths kept even).
  Player collision: `FUN_8002C41C`; leaf of a point: `FUN_80028E74`.
* Rest: leaf bitmask sizes and data used by dynamic lights.

The level's `.MAP` files are AI navigation grids (one byte per 128×128 cell, `0x81` = blocked), not player collision.

### Prototype BSP

Same header and nodes; 40-byte polygons `4 × {x, y, z, w}`, `uint16 uv3, uv2`, `uint32 flags` with
`tex = r0.w, pal = r1.w, uv0 = r2.w, uv1 = r3.w`; flags bit 31 gouraud (low 16 bits = entry in a colour table of
4 × BGR555), else flat BGR555; bit 30 semi-transparent and double-sided; **bit 28 compressed wall**: r0 → r1 top
edge, `r2 = (?, height, pal, pal)`, `r3` = 4 UVs. After the polygons a colour table (`count` + 8-byte entries); the
tail as in the release but with 44-byte collision quads.

## Level overlays and descriptors

`0x800ABBB8`: 18 × `(descriptor, overlay name)` in play order. Overlays load at `0x80122600`
(up to `0x80127674`, where `mot_base.ovr` begins — `level1.ovr` fills the area completely).

Descriptor (inside the overlay):

| Offset | |
|---|---|
| +00 | level name |
| +04 | TXD file |
| +08 / +0C | BSP count / list |
| +18 | init function — the level's logic is MIPS code (doors, alarms, lifts, keys) |
| +20 | → `{areas, 0x8009E224 spawn handlers, …}`; areas: one 12-byte entry per BSP: `count, ?, ?, ?, records*` |
| +28 | → patrol scripts: `(flag, pointer)` pairs |
| +30 | models to preload (int16 list, −1) |
| +48 | player start x, y, z (int32); +5C start angle (4096 = 360°) |
| +74 | → pickups (24 bytes each, ends with code `0x1B39`) |
| +7C | level archive (`.cdf`) |

**Spawn record** (20 bytes): `+0 id, +3 spawn mode, +B enemy type, +C x, y, z (int16, world / 256),
+12 script index`.

**Pickup**: `int16 item code, arg; int32 x, y, z; int32 ?, ?`.

**Patrol bytecode** — `uint16 op, arg` + operands: `1` start, `2` ?, `4` wait *arg* frames, `5` ? (8 bytes),
`8` call function (8), `9` jump (8, ends a script), `17` move to x, y, z (16), `18` call function at x, y, z (20).

## EXE tables

| Address | Entry | |
|---|---|---|
| `0x8009D930` | 24 bytes | models: `+0` name pointer (index 1 = `jaxsk.skn`) |
| `0x800AB258` | 30 bytes | 46 enemy types: `+0` model index, `+4` hit points; type 0 = player, 41–45 bosses |
| `0x8009F010` | 32 bytes | 39 items: `+0` model index; name/description pointers 8 bytes before the entry |
| `0x800ABBB8` | 8 bytes | 18 levels |

Only enemies whose model is in the level archive (and whose dictionary is in the level TXD) can be placed.
