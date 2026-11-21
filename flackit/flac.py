"""FLAC metadata blocks and frames (RFC 9639).

Frames are contiguous, so a frame ends where the next frame begins (or at the
end of the file, which may carry a trailing metadata tag).  A frame start is
only accepted where the frame header's CRC-8 checks out, and a frame is only
counted when the CRC-16 over the whole frame - header and footer included - is
zero.  Sync-code shaped bytes inside the audio data therefore never split or
invent a frame.
"""
import struct


BLOCK_TYPES = {0: "STREAMINFO", 1: "PADDING", 2: "APPLICATION", 3: "SEEKTABLE", 4: "VORBIS_COMMENT", 5: "CUESHEET", 6: "PICTURE"}
RATES = {1: 88200, 2: 176400, 3: 192000, 4: 8000, 5: 16000, 6: 22050, 7: 24000, 8: 32000, 9: 44100, 10: 48000, 11: 96000}
BITS = {1: 8, 2: 12, 4: 16, 5: 20, 6: 24, 7: 32}
MODES = {8: "left_side", 9: "right_side", 10: "mid_side"}

SEEK_PLACEHOLDER = 0xFFFFFFFFFFFFFFFF


class FlacError(ValueError):
    pass


def _crc_table(width, poly):
    top, mask, table = 1 << (width - 1), (1 << width) - 1, []
    for i in range(256):
        c = i << (width - 8)
        for _ in range(8):
            c = ((c << 1) ^ poly) if c & top else c << 1
        table.append(c & mask)
    return table


_CRC8, _CRC16 = _crc_table(8, 0x07), _crc_table(16, 0x8005)


def crc8(d):
    c = 0
    for b in d:
        c = _CRC8[c ^ b]
    return c


def crc16(d):
    c = 0
    for b in d:
        c = ((c << 8) & 0xFFFF) ^ _CRC16[(c >> 8) ^ b]
    return c


def _id3v2(data):
    """Return the size of any leading ID3v2 tag(s) and the offset after them."""
    pos = total = 0
    while data[pos:pos + 3] == b"ID3":
        if pos + 10 > len(data):
            raise FlacError("truncated ID3v2 tag")
        size = data[pos + 6:pos + 10]
        if any(b & 0x80 for b in size):
            raise FlacError("invalid ID3v2 tag size")
        n = 0
        for b in size:
            n = (n << 7) | b
        tag = 10 + n + (10 if data[pos + 5] & 0x10 else 0)  # optional footer
        if pos + tag > len(data):
            raise FlacError("truncated ID3v2 tag")
        pos += tag
        total += tag
    return total, pos


def metadata(data, pos):
    blocks = []
    info = None
    seek = placeholders = 0
    tags = []
    while True:
        if pos + 4 > len(data):
            raise FlacError("truncated metadata")
        head = data[pos]
        last, kind = head >> 7, head & 0x7F
        length = int.from_bytes(data[pos + 1:pos + 4], "big")
        if kind == 127:
            raise FlacError("invalid metadata block type")
        if pos + 4 + length > len(data):
            raise FlacError("truncated metadata")
        body = data[pos + 4:pos + 4 + length]
        blocks.append({"type": BLOCK_TYPES.get(kind, "RESERVED"), "length": length})
        if kind == 0:
            if length != 34:
                raise FlacError("invalid STREAMINFO length")
            mn, mx = struct.unpack_from(">HH", body, 0)
            fmin = int.from_bytes(body[4:7], "big")
            fmax = int.from_bytes(body[7:10], "big")
            packed = int.from_bytes(body[10:18], "big")
            info = {"min_block": mn, "max_block": mx, "min_frame": fmin, "max_frame": fmax,
                    "sample_rate": packed >> 44, "channels": ((packed >> 41) & 7) + 1,
                    "bits": ((packed >> 36) & 31) + 1,
                    "total_samples": packed & ((1 << 36) - 1)}
        elif kind == 3:
            if length % 18:
                raise FlacError("invalid SEEKTABLE length")
            for off in range(0, length, 18):
                if int.from_bytes(body[off:off + 8], "big") == 0xFFFFFFFFFFFFFFFF:
                    placeholders += 1
                else:
                    seek += 1
        elif kind == 4:
            if length < 8:
                raise FlacError("invalid VORBIS_COMMENT length")
            (n,) = struct.unpack_from("<I", body, 0)
            p = 4 + n
            if p + 4 > length:
                raise FlacError("invalid VORBIS_COMMENT length")
            (count,) = struct.unpack_from("<I", body, p)
            p += 4
            for _ in range(count):
                if p + 4 > length:
                    raise FlacError("invalid VORBIS_COMMENT length")
                (n,) = struct.unpack_from("<I", body, p)
                if p + 4 + n > length:
                    raise FlacError("invalid VORBIS_COMMENT length")
                tags.append(body[p + 4:p + 4 + n].decode("utf-8"))
                p += 4 + n
        pos += 4 + length
        if last:
            break
    if info is None or blocks[0]["type"] != "STREAMINFO":
        raise FlacError("STREAMINFO must be the first metadata block")
    return info, blocks, seek, placeholders, tags, pos


def _utf8_number(data, p):
    """Decode the UTF-8-style coded frame/sample number (up to 36 bits)."""
    if p >= len(data):
        return None, p
    b = data[p]
    if b < 0x80:
        return b, p + 1
    n = 0
    while n < 8 and b & (0x80 >> n):
        n += 1
    if n < 2 or n > 7:
        return None, p
    v = b & ((1 << (7 - n)) - 1)
    for k in range(1, n):
        if p + k >= len(data) or data[p + k] & 0xC0 != 0x80:
            return None, p
        v = (v << 6) | (data[p + k] & 0x3F)
    return v, p + n


def frame_header(data, p, info):
    """Return a frame header dict, or None when the bytes at p are not one.

    The header's CRC-8 is verified, so arbitrary sync-code shaped bytes inside
    audio data are rejected.
    """
    if p + 5 > len(data) or data[p] != 0xFF or data[p + 1] & 0xFE != 0xF8:
        return None
    variable = data[p + 1] & 1
    bs_code, sr_code = data[p + 2] >> 4, data[p + 2] & 15
    ch_code, bps_code = data[p + 3] >> 4, (data[p + 3] >> 1) & 7
    if data[p + 3] & 1:                  # reserved bit must be zero
        return None
    if bs_code == 0 or sr_code == 15 or bps_code == 3 or ch_code > 10:
        return None                      # reserved / invalid codes
    number, q = _utf8_number(data, p + 4)
    if number is None or number > (1 << 36) - 1:
        return None
    if bs_code == 1:
        block = 192
    elif bs_code <= 5:
        block = 576 << (bs_code - 2)
    elif bs_code == 6:                   # 8-bit block size, minus one
        if q + 1 > len(data):
            return None
        block = data[q] + 1
        q += 1
    elif bs_code == 7:                   # 16-bit block size, minus one
        if q + 2 > len(data):
            return None
        block = int.from_bytes(data[q:q + 2], "big") + 1
        q += 2
    else:
        block = 256 << (bs_code - 8)
    if sr_code == 0:
        rate = info["sample_rate"]
    elif sr_code == 12:                  # 8-bit kHz
        if q + 1 > len(data):
            return None
        rate = data[q] * 1000
        q += 1
    elif sr_code == 13:                  # 16-bit Hz
        if q + 2 > len(data):
            return None
        rate = int.from_bytes(data[q:q + 2], "big")
        q += 2
    elif sr_code == 14:                  # 16-bit tens of Hz
        if q + 2 > len(data):
            return None
        rate = int.from_bytes(data[q:q + 2], "big") * 10
        q += 2
    else:
        rate = RATES[sr_code]
    if rate == 0:
        return None
    bits = info["bits"] if bps_code == 0 else BITS[bps_code]
    if q + 1 > len(data) or crc8(data[p:q]) != data[q]:
        return None                      # header checksum
    mode = MODES.get(ch_code, "independent")
    channels = 2 if ch_code in MODES else ch_code + 1
    return {"variable": bool(variable), "block": block, "rate": rate, "number": number,
            "mode": mode, "channels": channels, "bits": bits, "end": q + 1}


def _is_header(data, q, info):
    """Return a frame header dict when one starts exactly at q, else None."""
    if q + 5 > len(data) or data[q] != 0xFF or data[q + 1] & 0xFE != 0xF8:
        return None
    return frame_header(data, q, info)


def _next_header(data, start, info):
    """Return the offset of the next valid frame header at or after start."""
    q, n = start, len(data)
    while q + 5 <= n:
        if data[q] == 0xFF and data[q + 1] & 0xFE == 0xF8 and frame_header(data, q, info):
            return q
        q += 1
    return None


def _follows(h, h2):
    """True when h2 is the frame that must follow h (per RFC 9639 numbering)."""
    if h["variable"] != h2["variable"]:
        return False
    step = h["block"] if h["variable"] else 1
    return h2["number"] == h["number"] + step


def _tag_length(data, q):
    """Return the length of a trailing metadata tag starting at q, or 0."""
    n = len(data) - q
    if data[q:q + 3] == b"TAG" and n in (128, 227):
        return n                                            # ID3v1 / ID3v1+
    if data[q:q + 3] == b"ID3" and n >= 10:
        size = data[q + 6:q + 10]
        if not any(b & 0x80 for b in size):
            k = 0
            for b in size:
                k = (k << 7) | b
            k += 10 + (10 if data[q + 5] & 0x10 else 0)
            if k == n:
                return n                                    # ID3v2
    return 0


def _frame_end(data, p, h, info):
    """Return (end offset, checksum ok) for the frame whose header is at p.

    The CRC-16 of the frame is accumulated from its header on.  A frame ends
    where that running CRC reaches zero *and* either a frame header starts or
    the file ends (possibly followed by a trailing metadata tag).  A sync-code
    shaped byte pattern inside the audio data cannot end a frame: the running
    CRC is not zero there.

    If the CRC never reaches zero the frame is damaged.  It is then taken to
    end at the first header carrying the coded number that must follow this
    frame, so a damaged frame does not throw off the walk over its neighbours.

    The scan is bounded by the largest size the frame could possibly have.
    """
    table = _CRC16
    crc = 0
    for b in data[p:h["end"]]:
        crc = ((crc << 8) & 0xFFFF) ^ table[(crc >> 8) ^ b]
    n = len(data)
    # Even uncompressed, a sample needs its bit depth plus a subframe header
    # and wasted bits; 16 spare bits per sample is a safe upper bound.
    stop = min(n, p + h["block"] * h["channels"] * ((h["bits"] + 16) // 8) + 4096)
    fallback, q = None, h["end"]
    while q <= stop:
        if crc == 0 and (q == n or _tag_length(data, q) == n - q
                         or _is_header(data, q, info) is not None):
            return q, True              # file, tag or next frame starts here
        if q == stop:
            break
        nxt = _is_header(data, q, info)
        if nxt is not None and crc and fallback is None and _follows(h, nxt):
            fallback = q                # where this damaged frame must end
        crc = ((crc << 8) & 0xFFFF) ^ table[(crc >> 8) ^ data[q]]
        q += 1
    return fallback, False


def _resync(data, start, info):
    """Offset of the next frame header at or after start whose extent is known."""
    q = start
    while True:
        q = _next_header(data, q, info)
        if q is None:
            return None
        end, _ = _frame_end(data, q, frame_header(data, q, info), info)
        if end is not None:
            return q
        q += 1


def scan(data):
    id3, pos = _id3v2(data)
    if data[pos:pos + 4] != b"fLaC":
        raise FlacError("not a FLAC stream")
    info, blocks, seek, placeholders, tags, pos = metadata(data, pos + 4)
    frames, bad = [], 0
    p = _next_header(data, pos, info)
    while p is not None and p < len(data):
        h = frame_header(data, p, info)
        if h is None:               # offset cannot begin a frame
            p = _next_header(data, p + 1, info)
            continue
        end, ok = _frame_end(data, p, h, info)
        h["valid"] = ok
        frames.append(h)
        if ok:
            p = end
        else:
            bad += 1              # valid header, whole-frame checksum does not agree
            p = end if end is not None else _resync(data, h["end"], info)
    if not frames:
        raise FlacError("no frames")
    good = [f for f in frames if f["valid"]]
    modes = {}
    for f in frames:
        modes[f["mode"]] = modes.get(f["mode"], 0) + 1
    return {"id3v2_bytes": id3, **info, "metadata": blocks, "seek_points": seek, "placeholders": placeholders,
            "tags": tags, "frames": len(good), "frame_samples": sum(f["block"] for f in good),
            "blocking": "variable" if frames[0]["variable"] else "fixed",
            "first_number": frames[0]["number"], "last_number": frames[-1]["number"],
            "rates": sorted({f["rate"] for f in frames}), "frame_bits": sorted({f["bits"] for f in frames}),
            "channel_modes": dict(sorted(modes.items())), "bad_frames": bad}
