"""RealMedia (RMFF) files: chunks, PROP, MDPR (RealAudio / RealVideo / multi-rate), CONT, DATA packets, INDX."""
import struct
from fractions import Fraction


class RmError(ValueError):
    pass


# DATA packet header sizes: version 0 ends with the key/reliable flags byte,
# version 1 inserts a u16 ASM rule and a u16 reserved field before the flags.
_PACKET_HEAD = {0: 12, 1: 14}
# Offset of the key/reliable flags byte within each packet header version.
_PACKET_FLAGS = {0: 11, 1: 13}
_KEYFRAME = 0x02

# PROP flag bits (u16 trailing the nine u32 fields).
_LIVE = 0x0004
_DOWNLOADABLE = 0x0008

# Logical file-info entries describe the presentation, they carry no media.
_FILEINFO = "logical-fileinfo"


def _u(fmt, data, pos):
    try:
        return struct.unpack_from(">" + fmt, data, pos)
    except struct.error:
        raise RmError("chunk runs past the end of the file at %d" % pos)


def _pstr(data, pos, width):
    n = int.from_bytes(data[pos:pos + width], "big")
    return data[pos + width:pos + width + n].decode("latin-1"), pos + width + n


def _audio(ts):
    """RealAudio type-specific header (.ra\\xfd), versions 3, 4 and 5."""
    if ts[:4] != b".ra\xfd":
        return None
    version, = _u("H", ts, 4)
    if version not in (4, 5):
        # Version 3 (and anything newer/unknown) only carries the magic and
        # version; the detailed fields below do not exist.
        return {"type": "audio", "codec": None, "ra_version": version}
    # The sample_rate / unknown / sample_size / channels u16 quad ends the
    # fixed prefix; version 5 inserts a six-byte field before it.
    p = 54 if version == 5 else 48
    rate, _, bits, channels = _u("HHHH", ts, p)
    p += 8
    if version == 4:
        # Two length-prefixed strings: compression name, then the FourCC.
        _, p = _pstr(ts, p, 1)
        codec, p = _pstr(ts, p, 1)
    else:
        # Version 5: the "genr" tag followed directly by the FourCC.
        p += 4
        codec = ts[p:p + 4].decode("latin-1")
    return {"type": "audio", "codec": codec, "ra_version": version, "sample_rate": rate, "bits": bits,
            "channels": channels}


def _video(ts):
    """RealVideo type-specific header: u32 length, VIDO, FourCC, geometry, fps."""
    if ts[4:8] != b"VIDO":
        return None
    codec = ts[8:12].decode("latin-1")
    w, h, _, _, fps_fixed = _u("HHHHI", ts, 12)
    # Frames per second is a 16.16 fixed-point value.
    return {"type": "video", "codec": codec, "width": w, "height": h,
            "fps": str(Fraction(fps_fixed, 65536))}


def _type_specific(ts):
    return _audio(ts) or _video(ts)


def _multi_rate_count(data, p, end):
    """Count the encodings described by an MDPR multi-rate (SureStream) block.

    The block, when present, is a u32 length followed by a u16 number of
    physical streams, a u32 size and one (u16 stream id, u32 bit rate) entry
    per physical stream.  The type-specific header already describes the first
    encoding, so a block naming N physical streams means N + 1 encodings.
    """
    if end - p < 4:
        return 1
    mlen, = _u("I", data, p)
    if mlen == 0 or p + 4 + mlen > end:
        return 1
    mp = p + 4
    physical, = _u("H", data, mp)
    mp += 2 + 4
    if mp + physical * 6 > p + 4 + mlen:
        return 1
    return physical + 1


def parse(data):
    if data[:4] != b".RMF":
        raise RmError("not a RealMedia file")
    out = {"duration": None, "preroll": None, "avg_bitrate": None, "streams": [], "title": None,
           "author": None, "copyright": None, "comment": None, "indexed": False, "data_chunks": 0,
           "live": None, "downloadable": None}
    pos = 0
    packets, payload, keyframes = {}, {}, {}
    while pos + 10 <= len(data):
        cid = data[pos:pos + 4]
        size, ver = _u("IH", data, pos + 4)
        if size < 10 or pos + size > len(data):
            raise RmError("chunk %r at %d has a bad size" % (cid, pos))
        b = pos + 10
        end = pos + size
        if cid == b"PROP":
            vals = _u("9I", data, b)
            # The nine u32 fields (36 bytes) are followed by stream count and
            # the flag u16.
            flags, = _u("H", data, b + 38)
            out.update(live=bool(flags & _LIVE), downloadable=bool(flags & _DOWNLOADABLE))
            out.update(avg_bitrate=vals[1], duration=str(Fraction(vals[5], 1000)), preroll=vals[6])
        elif cid == b"MDPR":
            num, = _u("H", data, b)
            avgb, maxb = _u("II", data, b + 2)
            p = b + 2 + 4 * 7
            name, p = _pstr(data, p, 1)
            mime, p = _pstr(data, p, 1)
            tlen, = _u("I", data, p)
            p += 4
            ts = data[p:p + tlen]
            p += tlen
            # Logical file-info entries are not media streams.
            if not (mime == _FILEINFO or mime.endswith("/" + _FILEINFO)):
                s = {"number": num, "mime": mime, "name": name,
                     "max_bitrate": maxb, "avg_bitrate": avgb}
                s.update(_type_specific(ts) or {"type": "other"})
                s["rates"] = _multi_rate_count(data, p, end)
                out["streams"].append(s)
        elif cid == b"CONT":
            # The four content-descriptor strings use 2-byte length prefixes.
            p = b
            for key in ("title", "author", "copyright", "comment"):
                out[key], p = _pstr(data, p, 2)
        elif cid == b"DATA":
            out["data_chunks"] += 1
            count, _ = _u("II", data, b)
            p = b + 8
            for _ in range(count):
                pver, plen, stream, _ts = _u("HHHI", data, p)
                head = _PACKET_HEAD.get(pver)
                if head is None or plen < head or p + plen > end:
                    raise RmError("bad DATA packet at %d" % p)
                flags = data[p + _PACKET_FLAGS[pver]]
                packets[stream] = packets.get(stream, 0) + 1
                payload[stream] = payload.get(stream, 0) + plen - head
                if flags & _KEYFRAME:
                    keyframes[stream] = keyframes.get(stream, 0) + 1
                p += plen
        elif cid == b"INDX":
            out["indexed"] = True
        pos += size
    for s in out["streams"]:
        s["packets"] = packets.get(s["number"], 0)
        s["keyframes"] = keyframes.get(s["number"], 0)
        s["payload_bytes"] = payload.get(s["number"], 0)
    return out
