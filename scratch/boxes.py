import struct,sys
from pathlib import Path

PARENTS = ("moov trak mdia minf stbl edts dinf mvex moof traf udta mfra").split()

def boxes(data, start, end, depth, out):
    off = start
    while off < end:
        if end - off < 8:
            break
        size = struct.unpack(">I", data[off:off+4])[0]
        typ = data[off+4:off+8].decode("latin1")
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", data[off+8:off+16])[0]
            hdr = 16
        elif size == 0:
            size = end - off
        out.append((depth, typ, off, size, hdr))
        if typ in PARENTS:
            boxes(data, off+hdr, off+size, depth+1, out)
        off += size

d = Path(sys.argv[1]).read_bytes()
out = []
boxes(d, 0, len(d), 0, out)
for depth, typ, off, size, hdr in out:
    print("  "*depth + "%s off=%d size=%d" % (typ, off, size))
