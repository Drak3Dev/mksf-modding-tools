"""SF.CDF archive: header {name[12], count, total_size}, entries {name[12], offset, size}, data 2048-aligned.

  python3 cdf.py list SF.CDF
  python3 cdf.py extract SF.CDF outdir
"""
import os
import struct
import sys

ALIGN = 2048

def read(data):
    name = data[:12].split(b"\0")[0].decode()
    count, total = struct.unpack_from("<II", data, 12)
    files = []
    for i in range(count):
        o = 20 + i * 20
        fname = data[o:o + 12].split(b"\0")[0].decode()
        off, size = struct.unpack_from("<II", data, o + 12)
        files.append((fname, off, size))
    return name, total, files

def get(data, fname):
    for n, off, size in read(data)[2]:
        if n.lower() == fname.lower():
            return data[off:off + size]
    raise KeyError(fname)

def guess_align(data):
    return ALIGN if read(data)[2][0][1] % ALIGN == 0 else 4

def replace(data, fname, new):
    """Rebuild the archive with one file replaced (others move as needed)."""
    name, total, files = read(data)
    if not any(n.lower() == fname.lower() for n, o, s in files):
        raise KeyError(fname)
    items = [(n, new if n.lower() == fname.lower() else data[o:o + s]) for n, o, s in files]
    return build(name, items, guess_align(data))

def replace_inplace(data, fname, new):
    """Replace a file without moving others; must fit in its aligned slot."""
    data = bytearray(data)
    name, total, files = read(data)
    for i, (n, off, size) in enumerate(files):
        if n.lower() == fname.lower():
            end = files[i + 1][1] if i + 1 < len(files) else len(data)
            if off + len(new) > end:
                raise ValueError("%s: %d bytes does not fit slot of %d" % (fname, len(new), end - off))
            data[off:end] = new + b"\0" * (end - off - len(new))
            struct.pack_into("<I", data, 20 + i * 20 + 16, len(new))
            return bytes(data)
    raise KeyError(fname)

def build(name, files, align=ALIGN):
    """files: list of (name, bytes). align: 2048 for SF.CDF (read by sectors), 4 for level archives (loaded to RAM)."""
    hdr = 20 + 20 * len(files)
    off = (hdr + align - 1) // align * align
    table, body = bytearray(), bytearray()
    for n, d in files:
        table += n.encode().ljust(12, b"\0")[:12] + struct.pack("<II", off + len(body), len(d))
        if align == ALIGN:
            body += d + b"\0" * (align - len(d) % align)   # original packer: always >= 1 pad byte here
        else:
            body += d + b"\0" * (-len(d) % align)
    out = name.encode().ljust(12, b"\0") + struct.pack("<II", len(files), off + len(body)) + table
    out += b"\0" * (off - len(out))
    return bytes(out + body)

if __name__ == "__main__":
    data = open(sys.argv[2], "rb").read()
    name, total, files = read(data)
    if sys.argv[1] == "list":
        print("%s: %d files, total %d (file %d)" % (name, len(files), total, len(data)))
        for n, off, size in files:
            print("%-14s %08X %9d" % (n, off, size))
    elif sys.argv[1] == "extract":
        os.makedirs(sys.argv[3], exist_ok=True)
        for n, off, size in files:
            open(os.path.join(sys.argv[3], n), "wb").write(data[off:off + size])
