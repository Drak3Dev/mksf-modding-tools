"""Raw PS1 disc image (.bin, MODE2/2352) access: ISO 9660 listing, file read, sector write with EDC/ECC."""
import struct
import sys

SECTOR_RAW = 2352
SECTOR_DATA = 2048
DATA_OFFSET = 24

# -------------------------------------------
# EDC / ECC for raw Mode 2 Form 1 sectors.
# -------------------------------------------

_edc_lut = []
_ecc_f_lut = [0] * 256
_ecc_b_lut = [0] * 256

def _init_tables():
    if _edc_lut:
        return
    for i in range(256):
        edc = i
        for j in range(8):
            edc = (edc >> 1) ^ (0xD8018001 if edc & 1 else 0)
        _edc_lut.append(edc)
        j = ((i << 1) ^ (0x11D if i & 0x80 else 0)) & 0xFF
        _ecc_f_lut[i] = j
        _ecc_b_lut[i ^ j] = i

def _edc(data):
    edc = 0
    lut = _edc_lut
    for b in data:
        edc = (edc >> 8) ^ lut[(edc ^ b) & 0xFF]
    return edc

def _ecc_block(sector, src, major_count, minor_count, major_mult, minor_inc, dest):
    size = major_count * minor_count
    for major in range(major_count):
        index = (major >> 1) * major_mult + (major & 1)
        ecc_a = 0
        ecc_b = 0
        for minor in range(minor_count):
            temp = sector[src + index]
            index += minor_inc
            if index >= size:
                index -= size
            ecc_a ^= temp
            ecc_b ^= temp
            ecc_a = _ecc_f_lut[ecc_a]
        ecc_a = _ecc_b_lut[_ecc_f_lut[ecc_a] ^ ecc_b]
        sector[dest + major] = ecc_a
        sector[dest + major + major_count] = ecc_a ^ ecc_b

def fix_sector(sector):
    _init_tables()
    struct.pack_into("<I", sector, 0x818, _edc(sector[0x10:0x818]))
    address = bytes(sector[12:16])
    sector[12:16] = b"\x00\x00\x00\x00"
    _ecc_block(sector, 0xC, 86, 24, 2, 86, 0x81C)
    _ecc_block(sector, 0xC, 52, 43, 86, 88, 0x8C8)
    sector[12:16] = address

def sector_ok(sector):
    """True if a Mode 2 Form 1 sector's EDC matches."""
    _init_tables()
    return struct.unpack_from("<I", sector, 0x818)[0] == _edc(sector[0x10:0x818])

# -------------------------------------------
# ISO 9660
# -------------------------------------------

class Entry:
    def __init__(self, path, lba, size, is_dir, rec_lba, rec_off):
        self.path, self.lba, self.size, self.is_dir = path, lba, size, is_dir
        self.rec_lba, self.rec_off = rec_lba, rec_off   # where the directory record lives

    def __repr__(self):
        return "%-32s lba=%7d size=%10d%s" % (self.path, self.lba, self.size, " <DIR>" if self.is_dir else "")

class Disc:
    def __init__(self, path, writable=False):
        self.path = path
        self.file = open(path, "r+b" if writable else "rb")

    def close(self):
        self.file.close()

    def read_sector(self, lba):
        self.file.seek(lba * SECTOR_RAW)
        return bytearray(self.file.read(SECTOR_RAW))

    def read(self, lba, size):
        self.file.seek(lba * SECTOR_RAW)
        n = (size + SECTOR_DATA - 1) // SECTOR_DATA
        raw = self.file.read(n * SECTOR_RAW)
        out = bytearray()
        for i in range(n):
            out += raw[i * SECTOR_RAW + DATA_OFFSET:i * SECTOR_RAW + DATA_OFFSET + SECTOR_DATA]
        return bytes(out[:size])

    def write(self, lba, data):
        """Write data (padded with zeros to whole sectors) as Form 1 sectors, fixing EDC/ECC."""
        for i in range(0, len(data), SECTOR_DATA):
            sector = self.read_sector(lba)
            chunk = data[i:i + SECTOR_DATA]
            chunk = chunk + b"\x00" * (SECTOR_DATA - len(chunk))
            sector[DATA_OFFSET:DATA_OFFSET + SECTOR_DATA] = chunk
            fix_sector(sector)
            self.file.seek(lba * SECTOR_RAW)
            self.file.write(sector)
            lba += 1

    def entries(self):
        pvd = self.read(16, SECTOR_DATA)
        root_lba, root_size = struct.unpack_from("<I", pvd, 158)[0], struct.unpack_from("<I", pvd, 166)[0]
        out = []
        self._walk(root_lba, root_size, "", out)
        return out

    def _walk(self, lba, size, prefix, out):
        data = self.read(lba, size)
        o = 0
        while o < len(data):
            length = data[o]
            if length == 0:
                o = (o // SECTOR_DATA + 1) * SECTOR_DATA
                continue
            nlen = data[o + 32]
            raw = data[o + 33:o + 33 + nlen]
            if raw not in (b"\x00", b"\x01"):
                name = raw.decode("latin-1").split(";")[0]
                e_lba = struct.unpack_from("<I", data, o + 2)[0]
                e_size = struct.unpack_from("<I", data, o + 10)[0]
                is_dir = bool(data[o + 25] & 2)
                e = Entry(prefix + name, e_lba, e_size, is_dir, lba + o // SECTOR_DATA, o % SECTOR_DATA)
                out.append(e)
                if is_dir:
                    self._walk(e_lba, e_size, prefix + name + "/", out)
            o += length

    def find(self, path):
        path = path.upper().strip("/")
        for e in self.entries():
            if e.path.upper() == path:
                return e
        raise KeyError(path)

    def read_file(self, path):
        e = self.find(path)
        return self.read(e.lba, e.size)

    def set_entry(self, e, lba=None, size=None):
        """Rewrite LBA and/or size (both-endian) in a file's directory record."""
        sec = bytearray(self.read(e.rec_lba, SECTOR_DATA))
        o = e.rec_off
        if lba is not None:
            struct.pack_into("<I", sec, o + 2, lba)
            struct.pack_into(">I", sec, o + 6, lba)
            e.lba = lba
        if size is not None:
            struct.pack_into("<I", sec, o + 10, size)
            struct.pack_into(">I", sec, o + 14, size)
            e.size = size
        self.write(e.rec_lba, bytes(sec))

    def write_form1(self, lba, data, only_changed=False):
        """Write data as Mode 2 Form 1 data sectors (subheader 0x08, last one 0x89 = EOF), fixing EDC/ECC."""
        n = (len(data) + SECTOR_DATA - 1) // SECTOR_DATA
        for i in range(n):
            sec = self.read_sector(lba + i)
            chunk = data[i * SECTOR_DATA:(i + 1) * SECTOR_DATA]
            chunk = chunk + b"\0" * (SECTOR_DATA - len(chunk))
            sub = bytes([0, 0, 0x89 if i == n - 1 else 0x08, 0]) * 2
            if only_changed and sec[16:24] == sub and sec[DATA_OFFSET:DATA_OFFSET + SECTOR_DATA] == chunk:
                continue
            sec[16:24] = sub
            sec[DATA_OFFSET:DATA_OFFSET + SECTOR_DATA] = chunk
            fix_sector(sec)
            self.file.seek((lba + i) * SECTOR_RAW)
            self.file.write(sec)

    def replace_file(self, path, data, pad_file="JUNK.PAD", log=print, only_changed=True):
        """Replace a file's contents. Stays in place if it fits before the next file on the disc,
        otherwise moves to the start of pad_file (which shrinks)."""
        e = self.find(path)
        others = [x for x in self.entries() if not x.is_dir and x.lba > e.lba]
        nxt = min((x.lba for x in others), default=e.lba + (e.size + SECTOR_DATA - 1) // SECTOR_DATA)
        sectors = (len(data) + SECTOR_DATA - 1) // SECTOR_DATA
        nxt_entry = min(others, key=lambda x: x.lba) if others else None
        if nxt_entry is not None and nxt_entry.path.upper() == pad_file.upper() and \
                sectors > nxt - e.lba and sectors <= nxt - e.lba + nxt_entry.size // SECTOR_DATA:
            grow = sectors - (nxt - e.lba)                # take sectors from the pad right after the file
            self.set_entry(nxt_entry, lba=nxt_entry.lba + grow, size=nxt_entry.size - grow * SECTOR_DATA)
            self.write_form1(e.lba, data, only_changed=only_changed)
            self.set_entry(e, size=len(data))
            log("%s: grew in place at LBA %d into %s (+%d sectors)" % (path, e.lba, pad_file, grow))
            return
        if sectors <= nxt - e.lba:
            self.write_form1(e.lba, data, only_changed=only_changed)
            self.set_entry(e, size=len(data))
            log("%s: written in place at LBA %d (%d of %d sectors)" % (path, e.lba, sectors, nxt - e.lba))
            return
        pad = self.find(pad_file)
        if sectors > pad.size // SECTOR_DATA:
            raise ValueError("%s does not fit into %s" % (path, pad_file))
        lba = pad.lba
        self.write_form1(lba, data)
        self.set_entry(pad, lba=pad.lba + sectors, size=pad.size - sectors * SECTOR_DATA)
        self.set_entry(e, lba=lba, size=len(data))
        log("%s: moved to LBA %d (start of %s, which shrank)" % (path, lba, pad_file))

def resolve_bin(path):
    """A .cue's (first) .bin, or the path itself."""
    if path.lower().endswith(".cue"):
        import os, re
        for line in open(path, encoding="latin-1"):
            m = re.match(r'\s*FILE\s+"(.+)"', line)
            if m:
                return os.path.join(os.path.dirname(path), m.group(1))
    return path

def write_cue(bin_path):
    import os
    cue = os.path.splitext(bin_path)[0] + ".cue"
    open(cue, "w").write('FILE "%s" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n' % os.path.basename(bin_path))
    return cue

if __name__ == "__main__":
    d = Disc(sys.argv[1])
    if len(sys.argv) > 3 and sys.argv[2] == "get":
        sys.stdout.buffer.write(d.read_file(sys.argv[3]))
    else:
        for e in d.entries():
            print(e)
