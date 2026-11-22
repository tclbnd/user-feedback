"""RealMedia (RMFF) files: chunks, PROP, MDPR (RealAudio / RealVideo / multi-rate), CONT, DATA packets, INDX."""
import struct
from fractions import Fraction


class RmError(ValueError):
    pass


def _u(fmt, data, pos):
    try:
        return struct.unpack_from(">" + fmt, data, pos)
    except struct.error:
        raise RmError("chunk runs past the end of the file at %d" % pos)


def _pstr(data, pos, width):
    n = int.from_bytes(data[pos:pos + width], "big")
    return data[pos + width:pos + width + n].decode("latin-1"), pos + width + n


def _audio(ts):
    if ts[:4] != b".ra\xfd":
        return None
    version, = _u("H", ts, 4)
    if version not in (4, 5):
        return {"type": "audio", "codec": None, "ra_version": version}
    p = 6 + 2 + 4 + 4 + 2 + 4 + 2 + 4 + 12 + 2 + 2 + 2 + 2
    if version == 5:
        p += 6  # version 5 headers carry six extra bytes before the sample rate
    rate, _, bits, channels = _u("HHHH", ts, p)
    p += 8
    if version == 5:
        # 4-byte interleaver id ("genr") followed by a 4-byte codec FourCC
        codec = ts[p + 4:p + 8].decode("latin-1")
    else:
        _, p = _pstr(ts, p, 1)
        codec, p = _pstr(ts, p, 1)
    return {"type": "audio", "codec": codec, "ra_version": version, "sample_rate": rate, "bits": bits,
            "channels": channels}


def _video(ts):
    if ts[4:8] != b"VIDO":
        return None
    codec = ts[8:12].decode("latin-1")
    w, h, _, _, fps = _u("HHHHI", ts, 12)
    # frames per second is stored as a 16.16 fixed-point value
    return {"type": "video", "codec": codec, "width": w, "height": h, "fps": str(Fraction(fps, 65536))}


def _type_specific(ts):
    return _audio(ts) or _video(ts)


def _logical_streams(ts):
    """Physical stream numbers listed in a logical (multi-rate / file-info) MDPR."""
    if len(ts) < 7:
        return []
    _, version = _u("IH", ts, 0)
    if version != 0:
        return []
    count = ts[6]
    nums = []
    p = 7
    for _i in range(count):
        num, = _u("H", ts, p)
        nums.append(num)
        p += 2
    return nums


def parse(data):
    if data[:4] != b".RMF":
        raise RmError("not a RealMedia file")
    out = {"duration": None, "preroll": None, "avg_bitrate": None, "streams": [], "title": None,
           "author": None, "copyright": None, "comment": None, "indexed": False, "data_chunks": 0}
    pos, mdprs = 0, []
    packets = {}
    keyframes = {}
    while pos + 10 <= len(data):
        cid = data[pos:pos + 4]
        size, ver = _u("IH", data, pos + 4)
        if size < 10 or pos + size > len(data):
            raise RmError("chunk %r at %d has a bad size" % (cid, pos))
        b = pos + 10
        if cid == b"PROP":
            vals = _u("9I", data, b)
            # duration is stored in milliseconds; report it in seconds
            out.update(avg_bitrate=vals[1], duration=str(Fraction(vals[5], 1000)), preroll=vals[6])
        elif cid == b"MDPR":
            num, = _u("H", data, b)
            p = b + 2 + 4 * 7
            name, p = _pstr(data, p, 1)
            mime, p = _pstr(data, p, 1)
            tlen, = _u("I", data, p)
            ts = data[p + 4:p + 4 + tlen]
            mdprs.append((num, name, mime, ts))
        elif cid == b"CONT":
            p = b
            for key in ("title", "author", "copyright", "comment"):
                out[key], p = _pstr(data, p, 2)
        elif cid == b"DATA":
            out["data_chunks"] += 1
            count, _ = _u("II", data, b)
            p = b + 8
            for _ in range(count):
                pver, plen, stream, ts = _u("HHHI", data, p)
                # version 0 packets have a 12-byte header, version 1 a 13-byte one
                flags = data[p + (11 if pver == 0 else 12)]
                packets[stream] = packets.get(stream, 0) + 1
                if flags & 2:
                    keyframes[stream] = keyframes.get(stream, 0) + 1
                p += plen
        elif cid == b"INDX":
            out["indexed"] = True
        pos += size
    by_num = {num: ts for num, _name, _mime, ts in mdprs}
    physical_of = {}  # physical stream number -> owning multi-rate stream number
    fileinfo = set()  # physical streams of the logical file-info entry
    for num, _name, mime, ts in mdprs:
        if mime == "logical-fileinfo":
            fileinfo.update(_logical_streams(ts))
        elif "multirate" in mime:
            for pnum in _logical_streams(ts):
                physical_of[pnum] = num
    for num, name, mime, ts in mdprs:
        if mime == "logical-fileinfo" or num in fileinfo or num in physical_of:
            continue
        s = {"number": num, "mime": mime, "name": name}
        if "multirate" in mime:
            phys = _logical_streams(ts)
            first = by_num.get(phys[0]) if phys else None
            s.update((first is not None and _type_specific(first)) or {"type": "other"})
            s["rates"] = len(phys) or 1
            s["packets"] = sum(packets.get(pnum, 0) for pnum in phys)
            s["keyframes"] = sum(keyframes.get(pnum, 0) for pnum in phys)
        else:
            s.update(_type_specific(ts) or {"type": "other"})
            s["rates"] = 1
            s["packets"] = packets.get(num, 0)
            s["keyframes"] = keyframes.get(num, 0)
        out["streams"].append(s)
    return out
