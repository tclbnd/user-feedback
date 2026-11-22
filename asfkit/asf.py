"""ASF (Windows Media) header objects: file and stream properties, content descriptions, metadata."""
import struct
import uuid
from fractions import Fraction

G = {k: uuid.UUID(v).bytes_le for k, v in {
    "header": "75B22630-668E-11CF-A6D9-00AA0062CE6C",
    "data": "75B22636-668E-11CF-A6D9-00AA0062CE6C",
    "file": "8CABDCA1-A947-11CF-8EE4-00C00C205365",
    "stream": "B7DC0791-A9B7-11CF-8EE6-00C00C205365",
    "content": "75B22633-668E-11CF-A6D9-00AA0062CE6C",
    "ext_content": "D2D0A440-E307-11D2-97F0-00A0C95EA850",
    "extension": "5FBF03B5-A92E-11CF-8EE3-00C00C205365",
    "metadata": "C5F8CBEA-5BAF-4877-8467-AA8C44FA4CCA",
    "metadata_library": "44231C94-9498-49D1-A141-1D134E457054",
    "ext_stream": "14E6A5CB-C672-4332-8399-A96952065B5A",
    "audio": "F8699E40-5B4D-11CF-A8FD-00805F5C442B",
    "video": "BC19EFC0-5B4D-11CF-A8FD-00805F5C442B",
}.items()}


class AsfError(ValueError):
    pass


def objects(data, start, end):
    pos, out = start, []
    while pos + 24 <= end:
        gid = data[pos:pos + 16]
        size, = struct.unpack_from("<Q", data, pos + 16)
        if size < 24 or pos + size > end:
            raise AsfError("object at %d has a bad size" % pos)
        out.append((gid, pos + 24, pos + size))
        pos += size
    return out


def _utf16(b):
    return b.decode("utf-16-le", "replace").rstrip("\0")


def _value(vtype, raw, bool_size):
    if vtype == 0:  # Unicode string
        return _utf16(raw)
    if vtype == 2:  # BOOL: 4 bytes in content descriptors, 2 in metadata records
        return bool(int.from_bytes(raw[:bool_size], "little"))
    if vtype == 3:  # DWORD
        return int.from_bytes(raw[:4], "little")
    if vtype == 4:  # QWORD
        return int.from_bytes(raw[:8], "little")
    if vtype == 5:  # WORD
        return int.from_bytes(raw[:2], "little")
    if vtype == 6:  # GUID
        return str(uuid.UUID(bytes_le=raw[:16]))
    return raw.hex()  # byte array (type 1) and anything unknown


def parse(data):
    if data[:16] != G["header"]:
        raise AsfError("not an ASF file")
    hsize, = struct.unpack_from("<Q", data, 16)
    out = {"duration": None, "preroll": None, "packets": None, "packet_size": None, "broadcast": None,
           "seekable": None, "max_bitrate": None, "streams": [], "title": None, "author": None,
           "copyright": None, "description": None, "tags": {}}
    frame_times = {}  # stream number -> average time per frame (100 ns units)

    def tag(name, value):
        if name in out["tags"]:
            cur = out["tags"][name]
            if isinstance(cur, list):
                cur.append(value)
            else:
                out["tags"][name] = [cur, value]
        else:
            out["tags"][name] = value

    def handle(gid, b, e):
        if gid == G["file"]:
            (size, created, packets, play, send, preroll, flags,
             minp, maxp, rate) = struct.unpack_from("<QQQQQQIIII", data, b + 16)
            broadcast = bool(flags & 1)
            out.update(packets=None if broadcast else packets, preroll=preroll, broadcast=broadcast,
                       seekable=bool(flags & 2), packet_size=minp if minp == maxp else None, max_bitrate=rate)
            if not broadcast:
                # play duration includes the preroll; subtract it (preroll ms -> 100 ns units)
                out["duration"] = str(Fraction(play - preroll * 10_000, 10_000_000))
        elif gid == G["stream"]:
            stype = data[b:b + 16]
            tlen, elen, flags = struct.unpack_from("<IIH", data, b + 40)
            t = b + 54
            s = {"number": flags & 0x7F, "encrypted": bool(flags & 0x8000)}
            if stype == G["audio"]:
                tag_, ch, rate, avg, align, bits = struct.unpack_from("<HHIIHH", data, t)
                s.update(type="audio", codec="0x%04x" % tag_, channels=ch, sample_rate=rate, bits=bits)
            elif stype == G["video"]:
                w, h = struct.unpack_from("<II", data, t)
                fourcc = data[t + 11 + 16:t + 11 + 20].decode("latin-1")
                s.update(type="video", codec=fourcc, width=w, height=h, frame_rate=None)
            else:
                s.update(type="other")
            out["streams"].append(s)
        elif gid == G["content"]:
            lens = struct.unpack_from("<5H", data, b)
            p = b + 10
            for key, n in zip(("title", "author", "copyright", "description", None), lens):
                if key:
                    out[key] = _utf16(data[p:p + n])
                p += n
        elif gid == G["ext_content"]:
            count, = struct.unpack_from("<H", data, b)
            p = b + 2
            for _ in range(count):
                nl, = struct.unpack_from("<H", data, p)
                name = _utf16(data[p + 2:p + 2 + nl]); p += 2 + nl
                vt, vl = struct.unpack_from("<HH", data, p); p += 4
                tag(name, _value(vt, data[p:p + vl], 4)); p += vl
        elif gid == G["extension"]:
            # Header Extension: 16-byte reserved GUID, 2-byte reserved, 4-byte data size
            for gid2, b2, e2 in objects(data, b + 22, e):
                handle(gid2, b2, e2)
        elif gid in (G["metadata"], G["metadata_library"]):
            count, = struct.unpack_from("<H", data, b)
            p = b + 2
            for _ in range(count):
                lang, stream, nl, vt, vl = struct.unpack_from("<HHHHI", data, p)
                p += 12
                name = _utf16(data[p:p + nl]); p += nl
                tag(name, _value(vt, data[p:p + vl], 2)); p += vl
        elif gid == G["ext_stream"]:
            sn, = struct.unpack_from("<H", data, b + 48)
            avg, = struct.unpack_from("<Q", data, b + 52)
            if avg:
                frame_times[sn] = avg

    for gid, b, e in objects(data, 30, hsize):
        handle(gid, b, e)
    for s in out["streams"]:
        if s["type"] == "video":
            avg = frame_times.get(s["number"])
            s["frame_rate"] = str(Fraction(10_000_000, avg)) if avg else None
    out["streams"].sort(key=lambda s: s["number"])
    return out
