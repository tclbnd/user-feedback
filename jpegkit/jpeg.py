"""JPEG files: marker segments, JFIF and Exif headers, frame header, tables, scans, restart markers."""
import struct

PROCESS = {0xC0: "baseline", 0xC1: "extended"}
TRANSFORMS = {0: "none", 1: "ycbcr", 2: "ycck"}


class JpegError(ValueError):
    pass


def subsampling(comps):
    if len(comps) == 1:
        return "gray"
    (h0, v0), rest = comps[0][1:3], [c[1:3] for c in comps[1:]]
    if any(r != rest[0] for r in rest):
        return None
    h, v = h0 // rest[0][0], v0 // rest[0][1]
    return {(1, 1): "4:4:4", (1, 2): "4:2:2", (2, 2): "4:2:0", (4, 1): "4:1:1", (2, 1): "4:4:0"}.get((h, v))


def parse(data):
    if data[:2] != b"\xff\xd8":
        raise JpegError("not a JPEG file")
    out = {"process": None, "width": None, "height": None, "precision": None, "components": 0, "subsampling": None,
           "jfif": None, "exif": None, "adobe_transform": None, "quant_tables": 0, "huffman_tables": 0,
           "restart_interval": 0, "scans": 0, "restart_markers": 0, "eoi": False, "trailing_bytes": 0}
    p, n = 2, len(data)
    while p < n:
        if data[p] != 0xFF:
            raise JpegError("expected a marker at %d" % p)
        while p < n and data[p] == 0xFF:                       # fill bytes before a marker
            p += 1
        if p >= n:
            break
        m = data[p]
        p += 1
        if m == 0xD9:
            out["eoi"] = True
            out["trailing_bytes"] = n - p
            break
        if 0xD0 <= m <= 0xD7 or m == 0x01:
            continue
        length, = struct.unpack_from(">H", data, p)
        seg = data[p + 2:p + length]
        p += length
        if m == 0xE0 and seg[:5] == b"JFIF\0":
            units, xd, yd = seg[7], *struct.unpack_from(">HH", seg, 8)
            out["jfif"] = {"version": "%d.%02d" % (seg[5], seg[6]), "units": ("none", "dpi", "dpcm")[units] if units < 3 else None,
                           "density": [xd, yd]}
        elif m == 0xDB:
            q = 0
            out["quant_tables"] += 1
        elif m == 0xDD:
            out["restart_interval"], = struct.unpack(">H", seg[:2])
        elif m in PROCESS:
            out["process"] = PROCESS[m]
            out["precision"], out["height"], out["width"], nc = struct.unpack_from(">BHHB", seg)
            comps = [(seg[6 + 3 * i], seg[7 + 3 * i] >> 4, seg[7 + 3 * i] & 15) for i in range(nc)]
            out["components"] = nc
            out["subsampling"] = subsampling(comps)
        elif m == 0xDA:
            out["scans"] += 1
            while p < n:                                       # entropy-coded data up to the next real marker
                if data[p] == 0xFF and p + 1 < n:
                    nxt = data[p + 1]
                    if nxt == 0x00 or nxt == 0xFF:
                        p += 1 if nxt == 0xFF else 2
                        continue
                    break
                p += 1
    return out
