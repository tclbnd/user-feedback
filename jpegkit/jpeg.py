"""JPEG files: marker segments, JFIF/Adobe/Exif headers, frame header, tables, scans, restart markers."""
import struct

PROCESS = {
    0xC0: "baseline",
    0xC1: "extended",
    0xC2: "progressive",
    0xC3: "lossless",
    0xC5: "differential_sequential",
    0xC6: "differential_progressive",
    0xC7: "differential_lossless",
    0xC9: "arithmetic_extended",
    0xCA: "arithmetic_progressive",
    0xCB: "arithmetic_lossless",
    0xCD: "arithmetic_differential_sequential",
    0xCE: "arithmetic_differential_progressive",
    0xCF: "arithmetic_differential_lossless",
}
TRANSFORMS = {0: "none", 1: "ycbcr", 2: "ycck"}

# sampling factors of the first component relative to the others
SAMPLING = {(1, 1): "4:4:4", (2, 1): "4:2:2", (1, 2): "4:4:0", (2, 2): "4:2:0", (4, 1): "4:1:1"}

# TIFF field types: size in bytes, and integer formats for the integer types
TIFF_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
TIFF_INTS = {1: "B", 3: "H", 4: "I", 6: "b", 8: "h", 9: "i"}


class JpegError(ValueError):
    pass


def subsampling(comps):
    if len(comps) == 1:
        return "gray"
    if len(comps) < 2:
        return None
    h0, v0 = comps[0][1:3]
    rest = [c[1:3] for c in comps[1:]]
    if not h0 or not v0 or any(r != rest[0] for r in rest):
        return None
    rh, rv = rest[0]
    if not rh or not rv or h0 % rh or v0 % rv:
        return None
    return SAMPLING.get((h0 // rh, v0 // rv))


def color_space(comps, jfif, adobe):
    """The colour space libjpeg-turbo assumes for the frame's components."""
    nc = len(comps)
    if nc == 1:
        return "gray"
    if nc == 3:
        if jfif:
            return "ycbcr"
        if adobe is not None:                          # APP14 transform: 0 = RGB, otherwise YCbCr
            return "rgb" if adobe == 0 else "ycbcr"
        if [c[0] for c in comps] == [0x52, 0x47, 0x42]:   # component IDs 'R', 'G', 'B'
            return "rgb"
        return "ycbcr"
    if nc == 4:
        if adobe is not None:                          # APP14 transform: 0 = CMYK, otherwise YCCK
            return "cmyk" if adobe == 0 else "ycck"
        return "cmyk"
    return None


def quant_tables(seg):
    """Number of tables in a DQT segment; each holds 64 one- or two-byte elements."""
    count, pos = 0, 0
    while pos < len(seg):
        pq = seg[pos] >> 4
        if pq > 1:
            raise JpegError("bad quantization table precision %d" % pq)
        pos += 1 + 64 * (pq + 1)
        count += 1
    if pos != len(seg):
        raise JpegError("truncated quantization table")
    return count


def huffman_tables(seg):
    """Number of tables in a DHT segment; each holds 16 code counts and that many symbols."""
    count, pos = 0, 0
    while pos < len(seg):
        if pos + 17 > len(seg):
            raise JpegError("truncated Huffman table")
        pos += 17 + sum(seg[pos + 1:pos + 17])
        count += 1
    if pos != len(seg):
        raise JpegError("truncated Huffman table")
    return count


def _u16(tiff, order, off):
    if off is None or off < 0 or off + 2 > len(tiff):
        return None
    return struct.unpack_from(order + "H", tiff, off)[0]


def _u32(tiff, order, off):
    if off is None or off < 0 or off + 4 > len(tiff):
        return None
    return struct.unpack_from(order + "I", tiff, off)[0]


def _ifd(tiff, order, off):
    """Entries of the IFD at off as {tag: (type, count, offset of the value field)}."""
    entries = {}
    count = _u16(tiff, order, off)
    if count is None:
        return entries
    count = min(count, max(0, (len(tiff) - off - 2) // 12))
    for i in range(count):
        e = off + 2 + 12 * i
        entries[_u16(tiff, order, e)] = (_u16(tiff, order, e + 2), _u32(tiff, order, e + 4), e + 8)
    return entries


def _field(tiff, order, entry, size):
    """Start of an entry's value bytes, or None when they lie outside the segment."""
    typ, count, field = entry
    if typ is None or count is None:
        return None
    total = size * count
    start = field if total <= 4 else _u32(tiff, order, field)
    if start is None or start < 0 or start + total > len(tiff):
        return None
    return start


def _text(tiff, order, entry):
    size = TIFF_SIZES.get(entry[0])
    start = _field(tiff, order, entry, size) if size else None
    if start is None:
        return None
    return tiff[start:start + size * entry[1]].split(b"\x00", 1)[0].decode("utf-8", "replace")


def _integer(tiff, order, entry):
    fmt = TIFF_INTS.get(entry[0])
    if fmt is None or not entry[1]:
        return None
    start = _field(tiff, order, entry, TIFF_SIZES[entry[0]])
    if start is None:
        return None
    return struct.unpack_from(order + fmt, tiff, start)[0]


def exif(seg):
    """Camera data from an Exif APP1 payload: byte order, IFD0 and the Exif IFD.

    Returns None when the payload does not start with a valid TIFF header; values
    that are missing or lie outside the segment come back as None.
    """
    tiff = seg[6:]
    if len(tiff) < 8:
        return None
    if tiff[:2] == b"II":
        order, byte_order = "<", "little"
    elif tiff[:2] == b"MM":
        order, byte_order = ">", "big"
    else:
        return None
    if _u16(tiff, order, 2) != 42:
        return None
    out = {"byte_order": byte_order, "make": None, "model": None, "orientation": None, "datetime_original": None}
    ifd0 = _ifd(tiff, order, _u32(tiff, order, 4))
    for tag, name in ((0x010F, "make"), (0x0110, "model")):
        if tag in ifd0:
            out[name] = _text(tiff, order, ifd0[tag])
    if 0x0112 in ifd0:
        out["orientation"] = _integer(tiff, order, ifd0[0x0112])
    if 0x8769 in ifd0:                                  # Exif IFD pointer
        exif_ifd = _ifd(tiff, order, _integer(tiff, order, ifd0[0x8769]))
        if 0x9003 in exif_ifd:                          # DateTimeOriginal
            out["datetime_original"] = _text(tiff, order, exif_ifd[0x9003])
    return out


def parse(data):
    if data[:2] != b"\xff\xd8":
        raise JpegError("not a JPEG file")
    out = {"process": None, "width": None, "height": None, "precision": None, "components": 0, "subsampling": None,
           "jfif": None, "exif": None, "adobe_transform": None, "color_space": None, "quant_tables": 0,
           "huffman_tables": 0, "restart_interval": 0, "scans": 0, "restart_markers": 0, "eoi": False,
           "trailing_bytes": 0}
    comps, jfif_seen, adobe = [], False, None
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
        if p + 2 > n:
            raise JpegError("truncated marker segment at %d" % p)
        length, = struct.unpack_from(">H", data, p)
        if length < 2 or p + length > n:
            raise JpegError("truncated marker segment at %d" % p)
        seg = data[p + 2:p + length]
        p += length
        if m == 0xE0 and len(seg) >= 12 and seg[:5] == b"JFIF\0":
            units, xd, yd = seg[7], *struct.unpack_from(">HH", seg, 8)
            jfif_seen = True
            out["jfif"] = {"version": "%d.%02d" % (seg[5], seg[6]), "units": ("none", "dpi", "dpcm")[units] if units < 3 else None,
                           "density": [xd, yd]}
        elif m == 0xE1 and seg[:6] == b"Exif\0\0":
            out["exif"] = exif(seg)
        elif m == 0xEE and len(seg) >= 12 and seg[:5] == b"Adobe":
            adobe = seg[11]
            out["adobe_transform"] = TRANSFORMS.get(adobe)
        elif m == 0xDB:
            out["quant_tables"] += quant_tables(seg)
        elif m == 0xC4:
            out["huffman_tables"] += huffman_tables(seg)
        elif m == 0xDD:
            if len(seg) < 2:
                raise JpegError("truncated restart interval")
            out["restart_interval"], = struct.unpack(">H", seg[:2])
        elif m in PROCESS:
            if len(seg) < 6:
                raise JpegError("truncated frame header")
            out["process"] = PROCESS[m]
            out["precision"], out["height"], out["width"], nc = struct.unpack_from(">BHHB", seg)
            if len(seg) < 6 + 3 * nc:
                raise JpegError("truncated frame header")
            comps = [(seg[6 + 3 * i], seg[7 + 3 * i] >> 4, seg[7 + 3 * i] & 15) for i in range(nc)]
            out["components"] = nc
            out["subsampling"] = subsampling(comps)
        elif m == 0xDA:
            out["scans"] += 1
            while p < n:                                       # entropy-coded data up to the next real marker
                ff = data.find(b"\xff", p)                     # run of ordinary entropy bytes
                if ff < 0:
                    p = n
                    break
                p = ff
                q = p + 1
                while q < n and data[q] == 0xFF:               # fill bytes before the marker
                    q += 1
                if q >= n:
                    p = q
                    break
                nxt = data[q]
                if nxt == 0x00:                                # stuffed data byte
                    p = q + 1
                elif 0xD0 <= nxt <= 0xD7:                      # restart marker
                    out["restart_markers"] += 1
                    p = q + 1
                else:
                    break
    out["color_space"] = color_space(comps, jfif_seen, adobe) if out["process"] else None
    return out
