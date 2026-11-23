"""Builders for small synthetic ProRes streams used by the tests."""
import struct


def picture(nslices=8, slice_size=200, phsize=8):
    table = struct.pack(">%dH" % nslices, *[slice_size + (i % 3) for i in range(nslices)])
    body = bytes(sum(slice_size + (i % 3) for i in range(nslices)))
    head = bytes([phsize << 3]) + struct.pack(">IH", phsize + len(table) + len(body), nslices) + bytes(phsize - 7)
    return head + table + body


def frame(width=1920, height=1080, chroma=2, scan=0, aspect=3, rate=2, color=(1, 1, 1), alpha=0, creator=b"apl0", slices=8):
    version, matrices, extra_header, bad_slice = 0, 0, 0, False
    hdr = struct.pack(">HH", 0, version) + creator + struct.pack(">HH", width, height)
    hdr += bytes([chroma << 6 | scan << 2, aspect << 4 | rate, color[0], color[1], color[2], alpha, 0, matrices])
    hdr += bytes(64 * bin(matrices & 3).count("1")) + bytes(extra_header)
    hdr = struct.pack(">H", len(hdr)) + hdr[2:]
    pics = b"".join(picture(slices) for _ in range(2 if scan else 1))
    if bad_slice:
        pics = pics[:9] + struct.pack(">H", 999) + pics[11:]
    body = b"icpf" + hdr + pics
    return struct.pack(">I", len(body) + 4) + body


def stream(n=10, **kw):
    cut = 0
    out = b"".join(frame(**kw) for _ in range(n))
    return out[:len(out) - cut] if cut else out
