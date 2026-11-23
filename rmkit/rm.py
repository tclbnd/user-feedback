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
    # .ra4/.ra5 common header ends right before the rate block.
    p = 6 + 2 + 4 + 4 + 2 + 4 + 2 + 4 + 12 + 2 + 2 + 2 + 2
    if version == 5:
        p += 6  # version-5 extension block (3 u16 reserved fields)
    rate, _, bits, channels = _u("HHHH", ts, p)
    p += 8
    if version == 4:
        _, p = _pstr(ts, p, 1)       # four-CC version tag, e.g. "Int4"
        codec, p = _pstr(ts, p, 1)
    else:
        p += 4                       # "genr" tag
        codec = ts[p:p + 4].decode("latin-1")
    return {"type": "audio", "codec": codec, "ra_version": version, "sample_rate": rate, "bits": bits,
            "channels": channels}


def _video(ts):
    if ts[4:8] != b"VIDO":
        return None
    codec = ts[8:12].decode("latin-1")
    w, h, _, _, fps = _u("HHHHI", ts, 12)
    return {"type": "video", "codec": codec, "width": w, "height": h, "fps": str(Fraction(fps, 65536))}


def _type_specific(ts):
    return _audio(ts) or _video(ts) or {"type": "other"}


def _mdpr_body(data, b):
    """Parse the body of an MDPR chunk (b is the first body byte)."""
    num, = _u("H", data, b)
    p = b + 2 + 4 * 7
    name, p = _pstr(data, p, 1)
    mime, p = _pstr(data, p, 1)
    tlen, = _u("I", data, p)
    p += 4
    ts = data[p:p + tlen]
    if len(ts) != tlen:
        raise RmError("MDPR type-specific data runs past the end of the chunk at %d" % p)
    return num, name, mime, ts


def _multipart(ts):
    """Type-specific data of a multi-rate (SureStream) MDPR: a count of inner MDPR chunks."""
    count, = _u("I", ts, 0)
    p, inner = 4, []
    for _ in range(count):
        if ts[p:p + 4] != b"MDPR":
            raise RmError("multi-rate stream is missing an inner MDPR chunk at %d" % p)
        size, = _u("I", ts, p + 4)
        if size < 10 or p + size > len(ts):
            raise RmError("inner MDPR chunk at %d has a bad size" % p)
        inner.append(_mdpr_body(ts, p + 10))
        p += size
    return inner


def parse(data):
    if data[:4] != b".RMF":
        raise RmError("not a RealMedia file")
    out = {"duration": None, "preroll": None, "avg_bitrate": None, "streams": [], "title": None,
           "author": None, "copyright": None, "comment": None, "indexed": False, "data_chunks": 0}
    pos, packets = 0, {}
    keyframes = {}
    # Physical stream numbers used in DATA packets -> position in out["streams"].
    # A multi-rate MDPR owns several inner stream numbers but counts once.
    owner = {}
    while pos + 10 <= len(data):
        cid = data[pos:pos + 4]
        size, ver = _u("IH", data, pos + 4)
        if size < 10 or pos + size > len(data):
            raise RmError("chunk %r at %d has a bad size" % (cid, pos))
        b = pos + 10
        if cid == b"PROP":
            vals = _u("9I", data, b)
            # duration and preroll are stored in milliseconds; report duration in seconds
            out.update(avg_bitrate=vals[1], duration=str(Fraction(vals[5], 1000)), preroll=vals[6])
        elif cid == b"MDPR":
            num, name, mime, ts = _mdpr_body(data, b)
            if mime == "logical-fileinfo":
                # Not a media stream: only holds file-level metadata, never DATA packets.
                pos += size
                continue
            if mime == "multipart/x-pn-realmedia":
                inner = _multipart(ts)
                if not inner:
                    raise RmError("multi-rate stream %d has no encodings" % num)
                num0, name, mime, ts0 = inner[0]
                for inum, _, _, _ in inner:
                    owner[inum] = len(out["streams"])
                rates = len(inner)
            else:
                owner[num] = len(out["streams"])
                ts0, rates = ts, 1
            s = {"number": num, "mime": mime, "name": name}
            s.update(_type_specific(ts0))
            s["rates"] = rates
            out["streams"].append(s)
        elif cid == b"CONT":
            p = b
            for key in ("title", "author", "copyright", "comment"):
                out[key], p = _pstr(data, p, 2)
        elif cid == b"DATA":
            out["data_chunks"] += 1
            count, _ = _u("II", data, b)
            p = b + 8
            end = pos + size
            for _ in range(count):
                if p + 12 > end:
                    raise RmError("DATA chunk at %d has a packet running past its end" % pos)
                pver, plen, stream, tsval = _u("HHHI", data, p)
                if plen < 12 or p + plen > end:
                    raise RmError("packet at %d has a bad length" % p)
                # v0 packets carry one flags byte at offset 11 (12-byte header);
                # v1 packets add a byte, moving the flags byte to offset 12 (13-byte header).
                flags = data[p + (12 if pver == 1 else 11)]
                idx = owner.get(stream)
                if idx is not None:
                    packets[idx] = packets.get(idx, 0) + 1
                    if flags & 2:
                        keyframes[idx] = keyframes.get(idx, 0) + 1
                p += plen
        elif cid == b"INDX":
            out["indexed"] = True
        pos += size
    for idx, s in enumerate(out["streams"]):
        s["packets"] = packets.get(idx, 0)
        s["keyframes"] = keyframes.get(idx, 0)
    return out
