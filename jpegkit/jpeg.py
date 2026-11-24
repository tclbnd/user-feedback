"""JPEG files: marker segments, JFIF and Exif headers, frame header, tables, scans, restart markers."""
import struct

PROCESS = {
    0xC0: "baseline", 0xC1: "extended", 0xC2: "progressive", 0xC3: "lossless",
    0xC5: "differential_sequential", 0xC6: "differential_progressive", 0xC7: "differential_lossless",
    0xC9: "arithmetic_extended", 0xCA: "arithmetic_progressive", 0xCB: "arithmetic_lossless",
    0xCD: "arithmetic_differential_sequential", 0xCE: "arithmetic_differential_progressive",
    0xCF: "arithmetic_differential_lossless",
}
TRANSFORMS = {0: "none", 1: "ycbcr", 2: "ycck"}


class JpegError(ValueError):
    pass


def subsampling(comps):
    if len(comps) == 1:
        return "gray"
    (h0, v0), rest = comps[0][1:3], [c[1:3] for c in comps[1:]]
    if any(r != rest[0] for r in rest):
        return None
    hr, vr = rest[0]
    if not hr or not vr or h0 % hr or v0 % vr:
        return None
    return {(1, 1): "4:4:4", (2, 1): "4:2:2", (2, 2): "4:2:0", (4, 1): "4:1:1", (1, 2): "4:4:0"}.get((h0 // hr, v0 // vr))


def exif(seg):
    tiff = seg[6:]
    if tiff[:2] == b"II":
        order = "<"
    elif tiff[:2] == b"MM":
        order = ">"
    else:
        raise JpegError("bad Exif byte order")
    u16 = lambda p: struct.unpack_from(order + "H", tiff, p)[0]
    u32 = lambda p: struct.unpack_from(order + "I", tiff, p)[0]
    if u16(2) != 42:
        raise JpegError("bad Exif TIFF magic")
    out = {"byte_order": "little" if order == "<" else "big", "make": None, "model": None, "orientation": None,
           "datetime_original": None}

    def ascii(p):
        count = u32(p + 4)
        off = p + 8 if count <= 4 else u32(p + 8)
        return tiff[off:off + count].split(b"\0")[0].decode("latin-1").strip() or None

    def ifd(off, names):
        n = u16(off)
        found = {}
        for i in range(n):
            p = off + 2 + 12 * i
            tag, typ = u16(p), u16(p + 2)
            if tag in names:
                if typ == 2:
                    found[tag] = ascii(p)
                elif typ == 3:
                    found[tag] = u16(p + 8)
                elif typ == 4:
                    found[tag] = u32(p + 8)
        return found
    top = ifd(u32(4), {0x010F, 0x0110, 0x0112, 0x8769})
    out.update(make=top.get(0x010F), model=top.get(0x0110), orientation=top.get(0x0112))
    if 0x8769 in top:
        out["datetime_original"] = ifd(top[0x8769], {0x9003}).get(0x9003)
    return out


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
        elif m == 0xE1 and seg[:6] == b"Exif\0\0":
            out["exif"] = exif(seg)
        elif m == 0xEE and seg[:5] == b"Adobe":
            out["adobe_transform"] = TRANSFORMS.get(seg[11])
        elif m == 0xDB:
            q = 0
            while q < len(seg):
                q += 1 + 64 * (2 if seg[q] >> 4 else 1)      # Pq: 0 -> 64 8-bit values, 1 -> 64 16-bit values
                out["quant_tables"] += 1
        elif m == 0xC4:
            q = 0
            while q < len(seg):
                q += 17 + sum(seg[q + 1:q + 17])
                out["huffman_tables"] += 1
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
                    if nxt == 0x00:                            # stuffed 0xFF data byte
                        p += 2
                        continue
                    if nxt == 0xFF:                            # fill byte
                        p += 1
                        continue
                    if 0xD0 <= nxt <= 0xD7:                    # restart marker, entropy data resumes after it
                        out["restart_markers"] += 1
                        p += 2
                        continue
                    break
                p += 1
    return out
