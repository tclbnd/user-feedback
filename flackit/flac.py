"""FLAC metadata blocks and frame headers (RFC 9639)."""
import struct


BLOCK_TYPES = {0: "STREAMINFO", 1: "PADDING", 2: "APPLICATION", 3: "SEEKTABLE", 4: "VORBIS_COMMENT", 5: "CUESHEET", 6: "PICTURE"}
RATES = {1: 88200, 2: 176400, 3: 192000, 4: 8000, 5: 16000, 6: 22050, 7: 24000, 8: 32000, 9: 44100, 10: 48000, 11: 96000}
BITS = {1: 8, 2: 12, 4: 16, 5: 20, 6: 24, 7: 32}
MODES = {8: "left_side", 9: "right_side", 10: "mid_side"}


class FlacError(ValueError):
    pass


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
        body = data[pos + 4:pos + 4 + length]
        if kind == 127:
            raise FlacError("invalid metadata block type")
        blocks.append({"type": BLOCK_TYPES.get(kind, "RESERVED"), "length": length})
        if kind == 0:
            mn, mx = struct.unpack_from(">HH", body, 0)
            fmin = int.from_bytes(body[4:7], "big")
            fmax = int.from_bytes(body[7:10], "big")
            packed = int.from_bytes(body[10:18], "big")
            info = {"min_block": mn, "max_block": mx, "min_frame": fmin, "max_frame": fmax,
                    "sample_rate": packed >> 44, "channels": ((packed >> 41) & 7) + 1,
                    "bits": ((packed >> 36) & 31) + 1, "total_samples": packed & 0xFFFFFFFF}
        elif kind == 3:
            seek += length // 18
        elif kind == 4:
            (n,) = struct.unpack_from("<I", body, 0)
            p = 4 + n
            (count,) = struct.unpack_from("<I", body, p)
            p += 4
            for _ in range(count):
                (n,) = struct.unpack_from("<I", body, p)
                tags.append(body[p + 4:p + 4 + n].decode("utf-8"))
                p += 4 + n
        pos += 4 + length
        if last:
            break
    if info is None or blocks[0]["type"] != "STREAMINFO":
        raise FlacError("STREAMINFO must be the first metadata block")
    return info, blocks, seek, placeholders, tags, pos


def _utf8_number(data, p):
    b = data[p]
    if b < 0x80:
        return b, p + 1
    n = 0
    while b & (0x80 >> n):
        n += 1
    if n < 2 or n > 6:
        return None, p
    v = b & 0x1F
    for k in range(1, n):
        if p + k >= len(data) or data[p + k] & 0xC0 != 0x80:
            return None, p
        v = (v << 6) | (data[p + k] & 0x3F)
    return v, p + n


def frame_header(data, p, info):
    """Return header dict or None when the bytes at p are not a valid frame header."""
    if p + 5 > len(data) or data[p] != 0xFF or data[p + 1] & 0xFE != 0xF8:
        return None
    variable = data[p + 1] & 1
    bs_code, sr_code = data[p + 2] >> 4, data[p + 2] & 15
    ch_code, bps_code = data[p + 3] >> 4, (data[p + 3] >> 1) & 7
    if bs_code == 0 or sr_code == 15:
        return None
    number, q = _utf8_number(data, p + 4)
    if number is None:
        return None
    if bs_code == 1:
        block = 192
    elif bs_code <= 5:
        block = 576 << (bs_code - 2)
    elif bs_code in (6, 7):
        block = data[q] + 1
        q += 1
    else:
        block = 256 << (bs_code - 8)
    if sr_code == 0:
        rate = info["sample_rate"]
    elif sr_code == 12:
        rate = data[q] * 1000
        q += 1
    elif sr_code in (13, 14):
        rate = int.from_bytes(data[q:q + 2], "big")
        q += 2
    else:
        rate = RATES[sr_code]
    return {"variable": bool(variable), "block": block, "rate": rate, "number": number,
            "mode": "independent", "channels": ch_code + 1,
            "bits": BITS.get(bps_code), "end": q + 1}


def scan(data):
    pos = 0
    id3 = 0
    if data[pos:pos + 4] != b"fLaC":
        raise FlacError("not a FLAC stream")
    info, blocks, seek, placeholders, tags, pos = metadata(data, pos + 4)
    heads = []
    p = pos
    while p < len(data) - 1:
        if data[p] == 0xFF and data[p + 1] & 0xFE == 0xF8:
            h = frame_header(data, p, info)
            if h:
                heads.append((p, h))
        p += 1
    frames, bad = [h for _, h in heads], 0
    if not frames:
        raise FlacError("no frames")
    modes = {}
    for f in frames:
        modes[f["mode"]] = modes.get(f["mode"], 0) + 1
    return {"id3v2_bytes": id3, **info, "metadata": blocks, "seek_points": seek, "placeholders": placeholders,
            "tags": tags, "frames": len(frames), "frame_samples": sum(f["block"] for f in frames),
            "blocking": "variable" if frames[0]["variable"] else "fixed",
            "first_number": frames[0]["number"], "last_number": frames[-1]["number"],
            "rates": sorted({f["rate"] for f in frames}), "frame_bits": sorted({f["bits"] for f in frames}),
            "channel_modes": dict(sorted(modes.items())), "bad_frames": bad}
