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
    rate, _, bits, channels = _u("HHHH", ts, p)
    p += 8
    _, p = _pstr(ts, p, 1)
    codec, p = _pstr(ts, p, 1)
    return {"type": "audio", "codec": codec, "ra_version": version, "sample_rate": rate, "bits": bits,
            "channels": channels}


def _video(ts):
    if ts[4:8] != b"VIDO":
        return None
    codec = ts[8:12].decode("latin-1")
    w, h, _, _, fps = _u("HHHHI", ts, 12)
    return {"type": "video", "codec": codec, "width": w, "height": h, "fps": str(fps)}


def _type_specific(ts):
    return _audio(ts) or _video(ts)


def parse(data):
    if data[:4] != b".RMF":
        raise RmError("not a RealMedia file")
    out = {"duration": None, "preroll": None, "avg_bitrate": None, "streams": [], "title": None,
           "author": None, "copyright": None, "comment": None, "indexed": False, "data_chunks": 0,
           "live": None, "downloadable": None}
    pos, packets = 0, {}
    payload = {}
    keyframes = {}
    while pos + 10 <= len(data):
        cid = data[pos:pos + 4]
        size, ver = _u("IH", data, pos + 4)
        if size < 10 or pos + size > len(data):
            raise RmError("chunk %r at %d has a bad size" % (cid, pos))
        b = pos + 10
        if cid == b"PROP":
            vals = _u("9I", data, b)
            flags = data[b + 38]
            out.update(live=bool(flags & 4), downloadable=bool(flags & 8))
            out.update(avg_bitrate=vals[1], duration=str(Fraction(vals[5])), preroll=vals[6])
        elif cid == b"MDPR":
            num, = _u("H", data, b)
            p = b + 2 + 4 * 7
            name, p = _pstr(data, p, 1)
            mime, p = _pstr(data, p, 1)
            tlen, = _u("I", data, p)
            ts = data[p + 4:p + 4 + tlen]
            avgb, maxb = _u("II", data, b + 2)
            s = {"number": num, "mime": mime, "name": name, "max_bitrate": maxb, "avg_bitrate": avgb}
            s.update(_type_specific(ts) or {"type": "other"})
            s.setdefault("rates", 1)
            out["streams"].append(s)
        elif cid == b"CONT":
            p = b
            for key in ("title", "author", "copyright", "comment"):
                out[key], p = _pstr(data, p, 1)
        elif cid == b"DATA":
            out["data_chunks"] += 1
            count, _ = _u("II", data, b)
            p = b + 8
            for _ in range(count):
                pver, plen, stream, ts = _u("HHHI", data, p)
                flags = data[p + 11]
                packets[stream] = packets.get(stream, 0) + 1
                payload[stream] = payload.get(stream, 0) + plen - 12
                if flags & 2:
                    keyframes[stream] = keyframes.get(stream, 0) + 1
                p += plen
            break
        elif cid == b"INDX":
            out["indexed"] = True
        pos += size
    for s in out["streams"]:
        s["packets"] = packets.get(s["number"], 0)
        s["keyframes"] = keyframes.get(s["number"], 0)
        s["payload_bytes"] = payload.get(s["number"], 0)
    return out
