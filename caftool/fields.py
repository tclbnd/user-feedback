"""Decoding of desc, data, pakt, chan and info chunks."""
import struct

from .chunks import CafError


def u32(b, p):
    return int.from_bytes(b[p:p + 4], "big")


def s32(b, p):
    return int.from_bytes(b[p:p + 4], "big", signed=True)


def s64(b, p):
    return int.from_bytes(b[p:p + 8], "big", signed=True)


def desc(body):
    """Audio Description chunk.

    ``mSampleRate`` is a big-endian Float64, not an integer.
    """
    if len(body) < 32:
        raise CafError("short desc chunk")
    rate = struct.unpack_from(">d", body, 0)[0]
    return {
        "sample_rate": rate,
        "format_id": body[8:12].decode("latin-1"),
        "format_flags": u32(body, 12),
        "bytes_per_packet": u32(body, 16),
        "frames_per_packet": u32(body, 20),
        "channels": u32(body, 24),
        "bits": u32(body, 28),
    }


def data(body):
    """Data chunk: a UInt32 edit count followed by the audio bytes."""
    if len(body) < 4:
        raise CafError("short data chunk")
    return {"edit_count": u32(body, 0), "audio": body[4:]}


def _vlq(body, pos):
    """Read one big-endian base-128 variable length integer."""
    value = 0
    for _ in range(10):
        if pos >= len(body):
            raise CafError("truncated pakt chunk")
        byte = body[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos
    raise CafError("bad variable-length integer in pakt chunk")


def pakt(body, frames_per_packet=0):
    """Packet Table chunk.

    Each packet entry holds its size, and - when the desc chunk reports zero
    frames per packet (variable frame size) - also the packet's frame count.
    """
    if len(body) < 24:
        raise CafError("short pakt chunk")
    packets = s64(body, 0)
    valid = s64(body, 8)
    priming = s32(body, 16)
    remainder = s32(body, 20)
    if packets < 0:
        raise CafError("bad packet count in pakt chunk")
    pos, sizes, counts = 24, [], []
    for _ in range(packets):
        size, pos = _vlq(body, pos)
        sizes.append(size)
        if frames_per_packet == 0:
            frames, pos = _vlq(body, pos)
            counts.append(frames)
    return {"packets": packets, "valid_frames": valid, "priming": priming,
            "remainder": remainder, "sizes": sizes, "counts": counts}


def chan(body):
    """Channel Layout chunk.

    ``mChannelLayoutTag`` normally packs the channel count into its low 16
    bits.  The two sentinel tags ``kAudioChannelLayoutTag_UseChannelBitmap``
    and ``kAudioChannelLayoutTag_UseChannelDescriptions`` leave that field
    zero and describe the layout with the bitmap or description array instead.
    """
    if len(body) < 12:
        raise CafError("short chan chunk")
    tag = u32(body, 0)
    bitmap = u32(body, 4)
    descriptions = u32(body, 8)
    count = tag & 0xFFFF
    if count == 0:
        count = bin(bitmap).count("1") if bitmap else descriptions
    return {"tag": tag, "bitmap": bitmap, "descriptions": descriptions,
            "channels": count}


def _cstrings(raw):
    out, start = [], 0
    for i, byte in enumerate(raw):
        if byte == 0:
            out.append(raw[start:i].decode("utf-8", "replace"))
            start = i + 1
    return out


def info(body):
    """Information chunk: UInt32 entry count then key/value C strings."""
    if len(body) < 4:
        raise CafError("short info chunk")
    count = u32(body, 0)
    strings = _cstrings(body[4:])
    out = {}
    for i in range(count):
        if 2 * i + 1 >= len(strings):
            raise CafError("truncated info chunk")
        out[strings[2 * i]] = strings[2 * i + 1]
    return out
