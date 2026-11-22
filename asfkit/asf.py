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
    "audio": "F8699E40-5B4D-11CF-A8FD-00805F5C442B",
    "video": "BC19EFC0-5B4D-11CF-A8FD-00805F5C442B",
    # Some Windows Media encoders emit their metadata under this identifier
    # inside the header extension, using the same value typing as the
    # Extended Content Description object.
    "metadata_alt": "44231C94-9498-49D1-A141-1D134E457054",
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
    # ASF text is UTF-16LE, normally NUL terminated; the lengths count bytes.
    return b.decode("utf-16-le", "replace").rstrip("\0")


def _value(vtype, raw, bool_size):
    """Decode a typed ASF attribute value.

    ``bool_size`` is 4 for Extended Content Description values and 1 for
    Metadata Library values, matching the two ASF specifications.
    """
    if vtype == 0:       # Unicode string
        return _utf16(raw)
    if vtype == 1:       # byte array
        return raw.hex()
    if vtype == 2:       # BOOL
        return int.from_bytes(raw[:bool_size], "little", signed=False) != 0
    if vtype == 3:       # DWORD (32-bit unsigned)
        return int.from_bytes(raw[:4], "little", signed=False)
    if vtype == 4:       # QWORD (64-bit unsigned)
        return int.from_bytes(raw[:8], "little", signed=False)
    if vtype == 5:       # WORD (16-bit unsigned)
        return int.from_bytes(raw[:2], "little", signed=False)
    if vtype == 6:       # GUID
        return str(uuid.UUID(bytes_le=raw[:16]))
    return raw.hex()

def _add_tag(tags, name, value):
    """Record an attribute, turning repeated names into an ordered list."""
    if name in tags:
        old = tags[name]
        tags[name] = old + [value] if isinstance(old, list) else [old, value]
    else:
        tags[name] = value


def _ext_content(data, b, e, tags):
    """Extended Content Description object (MS-ASF 3.3)."""
    count, = struct.unpack_from("<H", data, b)
    p = b + 2
    for _ in range(count):
        nl, = struct.unpack_from("<H", data, p)
        name = _utf16(data[p + 2:p + 2 + nl]); p += 2 + nl
        vt, vl = struct.unpack_from("<HH", data, p); p += 4
        _add_tag(tags, name, _value(vt, data[p:p + vl], 4)); p += vl
    if p != e:
        raise AsfError("bad extended content description length")


def _find_name(data, p, nl, end):
    """Find a decodable UTF-16LE name of ``nl`` bytes in a record header.

    Encoders place between one and four padding words between the record
    leader and the name, so the name starts 8..14 bytes after the leader.
    The right offset is the one whose bytes decode as a non-empty UTF-16LE
    string ending in the terminating NUL.
    """
    if nl < 4:
        raise AsfError("malformed metadata descriptor at %d" % p)
    for hdr in range(8, 15, 2):
        ns = p + hdr
        if ns + nl > end:
            break
        cand = data[ns:ns + nl]
        if not cand.endswith(b"\x00\x00"):
            continue
        try:
            text = cand.decode("utf-16-le")
        except UnicodeDecodeError:
            continue
        if text.rstrip("\0"):
            return ns
    raise AsfError("malformed metadata descriptor at %d" % p)


def _find_leader(data, start, end):
    """Locate the next three-word descriptor leader at or after ``start``.

    A candidate is accepted only when a decodable NUL-terminated name of
    exactly the declared length follows its padding words.  Scanning word by
    word from ``start`` naturally skips the preceding descriptor's value
    bytes and padding.
    """
    q = start
    while q + 8 <= end:
        _vtype, _hint, nl = struct.unpack_from("<HHH", data, q)
        if nl and nl % 2 == 0:
            try:
                ns = _find_name(data, q, nl, end)
            except AsfError:
                ns = None
            if ns is not None:
                return q
        q += 2
    return end


def _scan_descriptors(data, b, e):
    """Parse the descriptor framing shared by the non-conforming encoders.

    Each descriptor starts with a three-word leader (value type, length
    hint, name byte length), followed by padding words, the UTF-16LE name,
    and then a raw value.  The leader fields are not reliable across
    encoders, so descriptor boundaries are recovered by scanning for the
    next valid leader; the last value ends at the object boundary.
    """
    count, = struct.unpack_from("<H", data, b)
    if count == 0:
        if b + 2 != e:
            raise AsfError("bad metadata object length")
        return []
    p = b + 2
    records = []
    for i in range(count):
        _vtype, _hint, nl = struct.unpack_from("<HHH", data, p)
        ns = _find_name(data, p, nl, e)
        name = _utf16(data[ns:ns + nl])
        name_end = ns + nl
        value_end = _find_leader(data, name_end, e) if i + 1 < count else e
        records.append((_vtype, name, data[name_end:value_end]))
        p = value_end
    if p != e:
        raise AsfError("bad metadata object length")
    return records


def _infer_type(raw):
    """Infer an ASF value type for typeless descriptor values."""
    n = len(raw)
    if n == 2:
        return 5
    if n == 4:
        return 3
    if n == 8:
        return 4
    if n and n % 2 == 0:
        return 0
    return 1


def _metadata(data, b, e, tags):
    """Metadata Library object (MS-ASF 3.4) plus encoder reorderings.

    The specification orders each descriptor as reserved word, stream
    number, name length, name, value type, value length, value.  Some
    Windows Media encoders instead lead with the value type, a length hint
    and the name length, padding the gap to the name; those are recovered
    with the self-validating descriptor scanner.
    """
    count, = struct.unpack_from("<H", data, b)
    if count == 0:
        if b + 2 != e:
            raise AsfError("bad metadata object length")
        return
    p = b + 2
    spec_records = 0
    while spec_records < count:
        reserved, stream, nl = struct.unpack_from("<HHH", data, p)
        after = p + 6 + nl
        spec_order = (reserved == 0 and stream <= 0x7F and nl % 2 == 0 and after + 6 <= e
                      and struct.unpack_from("<H", data, after)[0] <= 6
                      and after + 6 + struct.unpack_from("<I", data, after + 2)[0] <= e)
        if not spec_order:
            break
        name = _utf16(data[p + 6:after])
        vtype, vlen = struct.unpack_from("<HI", data, after)
        _add_tag(tags, name, _value(vtype, data[after + 6:after + 6 + vlen], 1))
        p = after + 6 + vlen
        spec_records += 1
    if spec_records == count:
        if p != e:
            raise AsfError("bad metadata object length")
        return
    # Non-conforming encoder framing.  The shuffled descriptors are scanned
    # from the object start; the leader's type word is not trustworthy across
    # encoders, so each value's type is derived from its width.
    for _vtype, name, raw in _scan_descriptors(data, b, e):
        _add_tag(tags, name, _value(_infer_type(raw), raw, 1))


def _metadata_alt(data, b, e, tags):
    """Encoder-specific metadata object found in the header extension.

    It shares the shuffled descriptor framing of the reordered Metadata
    Library object (three-word leader, padding, UTF-16LE name, raw value)
    but carries no typed values, so each value's type is inferred from its
    width.
    """
    for _vtype, name, raw in _scan_descriptors(data, b, e):
        _add_tag(tags, name, _value(_infer_type(raw), raw, 1))


def parse(data):
    if data[:16] != G["header"]:
        raise AsfError("not an ASF file")
    hsize, = struct.unpack_from("<Q", data, 16)
    if hsize < 30 or hsize > len(data):
        raise AsfError("bad header size")
    out = {"duration": None, "preroll": None, "packets": None, "packet_size": None, "broadcast": None,
           "seekable": None, "max_bitrate": None, "streams": [], "title": None, "author": None,
           "copyright": None, "description": None, "tags": {}}

    def walk(objs):
        for gid, b, e in objs:
            if gid == G["file"]:
                # Layout: 16-byte file ID, creation time, packets, play/send
                # durations, preroll, flags, min/max packet size, max bitrate.
                (_file_id, created, data_packets, packets, play, send, preroll, flags,
                 minp, maxp, rate) = struct.unpack_from("<16sQQQQQQIIII", data, b)
                broadcast = bool(flags & 1)
                out.update(packets=None if broadcast else packets, preroll=preroll, broadcast=broadcast,
                           seekable=bool(flags & 2), packet_size=minp if minp == maxp else None,
                           max_bitrate=rate)
                if not broadcast:
                    # Play duration includes preroll; report actual playback time.
                    out["duration"] = str(Fraction(play - preroll * 10_000, 10_000_000))
            elif gid == G["stream"]:
                stype = data[b:b + 16]
                tlen, elen, flags = struct.unpack_from("<IIH", data, b + 40)
                t = b + 54
                number = flags & 0x7F
                s = {"number": number, "encrypted": bool(flags & 0x8000)}
                if stype == G["audio"]:
                    tag, ch, rate, avg, align, bits = struct.unpack_from("<HHIIHH", data, t)
                    s.update(type="audio", codec="0x%04x" % tag, channels=ch, sample_rate=rate, bits=bits)
                elif stype == G["video"]:
                    w, h = struct.unpack_from("<II", data, t)
                    # VIDEOINFO: 4-byte size, two 4-byte rects, then BITMAPINFOHEADER
                    # whose compression FourCC sits 16 bytes into the header.
                    fourcc = data[t + 11 + 16:t + 11 + 20].decode("latin-1", "replace")
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
                _ext_content(data, b, e, out["tags"])
            elif gid == G["metadata"]:
                _metadata(data, b, e, out["tags"])
            elif gid == G["metadata_alt"]:
                _metadata_alt(data, b, e, out["tags"])
            elif gid == G["extension"]:
                # Header Extension Object: reserved GUID, reserved word,
                # then the size of the inner object list.
                esize, = struct.unpack_from("<I", data, b + 18)
                inner_start, inner_end = b + 22, b + 22 + esize
                if inner_end > e:
                    raise AsfError("bad header extension size")
                walk(objects(data, inner_start, inner_end))

    walk(objects(data, 30, hsize))
    out["streams"].sort(key=lambda s: s["number"])
    return out
