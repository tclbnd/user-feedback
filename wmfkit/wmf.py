"""Windows Metafiles (.wmf): placeable header, metafile header and the record stream."""
import struct

PLACEABLE_KEY = 0x9AC6CDD7
META_EOF, SETWINDOWORG, SETWINDOWEXT, TEXTOUT, EXTTEXTOUT, POLYGON = 0x0000, 0x020B, 0x020C, 0x0521, 0x0A32, 0x0324
ETO_OPAQUE = 0x0002
ETO_CLIPPED = 0x0004


class WmfError(ValueError):
    pass


def parse(data):
    out = {"placeable": False, "bounds": None, "inch": None, "size_mm": None, "checksum_ok": None}
    p = 0
    if len(data) >= 22 and struct.unpack_from("<I", data, 0)[0] == PLACEABLE_KEY:
        words = struct.unpack_from("<11H", data, 0)
        left, top, right, bottom = struct.unpack_from("<4h", data, 6)
        inch = words[7]
        checksum = 0
        for word in words[:10]:
            checksum ^= word
        out.update(placeable=True, bounds=[left, top, right, bottom], inch=inch,
                   checksum_ok=(checksum & 0xFFFF) == words[10],
                   size_mm=["%.1f" % ((right - left) * 25.4 / inch),
                            "%.1f" % ((bottom - top) * 25.4 / inch)])
        p = 22
    if len(data) < p + 18:
        raise WmfError("not a metafile")
    mtype, head_words, version, size_words, objects, max_record, members = struct.unpack_from("<HHHIHIH", data, p)
    if mtype not in (1, 2) or head_words != 9:
        raise WmfError("bad metafile header")
    q = p + 18
    records, biggest, polygons, points = 0, 0, 0, 0
    texts, org, ext, eof = [], None, None, False
    while q + 6 <= len(data):
        size, func = struct.unpack_from("<IH", data, q)
        record_bytes = size * 2
        if size < 3 or q + record_bytes > len(data):
            break
        records += 1
        biggest = max(biggest, size)
        par = q + 6
        if func == META_EOF:
            eof = True
            break
        if func == SETWINDOWORG:
            y, x = struct.unpack_from("<hh", data, par)
            org = [x, y]
        elif func == SETWINDOWEXT:
            y, x = struct.unpack_from("<hh", data, par)
            ext = [x, y]
        elif func == TEXTOUT:
            n = struct.unpack_from("<h", data, par)[0]
            texts.append(data[par + 2:par + 2 + n].decode("cp1252"))
        elif func == EXTTEXTOUT:
            y, x, n, opts = struct.unpack_from("<hhhH", data, par)
            s = par + 8
            if opts & (ETO_OPAQUE | ETO_CLIPPED):
                s += 8
            texts.append(data[s:s + n].decode("cp1252"))
        elif func == POLYGON:
            polygons += 1
            points += struct.unpack_from("<h", data, par)[0]
        q += record_bytes
    body = len(data) - p
    out.update(version="%d.%d" % (version >> 8, version & 0xFF), records=records, eof=eof,
               size_ok=size_words * 2 == body, max_record_ok=max_record == biggest, objects=objects,
               window_origin=org, window_extent=ext, polygons=polygons, polygon_points=points, texts=texts)
    return out
