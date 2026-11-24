"""Nintendo BRSTM streams: header, HEAD chunk references, stream / track / channel info, blocks.

Layout follows the BRSTM container as the Wii SDK writes it.

File header (0x40 bytes):
    0x00 RSTM magic, 0x04 byte order mark (0xfeff big, 0xfffe little),
    0x06 major/minor version, 0x08 file size, 0x0c header size (0x40),
    0x10 HEAD chunk offset / 0x14 size, 0x20 DATA chunk offset / 0x24 size.

The HEAD chunk starts with a table of three 8-byte references:
    u8 type, u8 pad[3], u32 offset.  Each offset is relative to the start of
    its own reference entry, so the referenced block lives at ``entry + offset``.
    They point at the stream information, the track information and the channel
    information, in that order.
"""
import struct
from fractions import Fraction

# Wii SDK BRSTM codec identifiers.
CODECS = {0: "pcm8", 1: "pcm16", 2: "dsp_adpcm"}


class RstmError(ValueError):
    pass


def parse(data):
    if len(data) < 0x40 or data[:4] != b"RSTM":
        raise RstmError("not a BRSTM stream")
    bom = data[4:6]
    e = ">" if bom == b"\xfe\xff" else "<" if bom == b"\xff\xfe" else None
    if e is None:
        raise RstmError("bad byte order mark")
    u8 = lambda p: data[p]
    u16 = lambda p: struct.unpack_from(e + "H", data, p)[0]
    u32 = lambda p: struct.unpack_from(e + "I", data, p)[0]
    s16 = lambda p: struct.unpack_from(e + "h", data, p)[0]

    version = u16(6)
    head_off, head_size = u32(0x10), u32(0x14)
    data_off, data_size = u32(0x20), u32(0x24)
    if head_off + 8 > len(data) or data[head_off:head_off + 4] != b"HEAD":
        raise RstmError("HEAD chunk not found")

    # HEAD reference table: offsets are relative to each 8-byte entry.
    def ref(i):
        entry = head_off + 8 + 8 * i
        return entry + u32(entry + 4)

    s = ref(0)
    t = ref(1)
    c = ref(2)

    # Stream information (STRMHEAD, 0x34 bytes).
    codec = u8(s + 0)
    loop = u8(s + 1)
    channels = u8(s + 2)
    # Sample rate is a 24-bit value at s+3 (high, middle, low byte).
    rate = (u8(s + 3) << 16) | (u8(s + 4) << 8) | u8(s + 5)
    loop_start = u32(s + 8)
    total = u32(s + 12)
    blocks = u32(s + 20)
    block_size = u32(s + 24)
    block_samples = u32(s + 28)
    last_size = u32(s + 32)
    last_samples = u32(s + 36)
    last_padded = u32(s + 40)
    seek_interval = u32(s + 48)

    # Track information: count, entry type, then one entry per track.
    # Type 1 entries are one byte; every other layout uses two-byte entries.
    tracks = u8(t)
    track_type = u8(t + 1)
    track_entry_size = 1 if track_type == 1 else 2

    # Channel information follows the track table directly: a channel count,
    # three padding bytes, then one 8-byte reference per listed channel.  The
    # reference table in the HEAD chunk may carry a null offset, so derive the
    # block position from the track table as well.
    if c and data[c:c + 1] and u8(c) == channels:
        chan_base = c
    else:
        chan_base = t + 2 + tracks * track_entry_size
    listed_channels = u8(chan_base)

    # ADPCM channel information, one block per listed channel.
    adpcm = None
    if codec == 2:
        adpcm = []
        for i in range(listed_channels):
            entry = chan_base + 4 + 8 * i
            info = entry + u32(entry + 4)
            coef = [s16(info + 2 * j) for j in range(16)]
            adpcm.append({
                "coef": coef[:2],
                "gain": s16(info + 32),
                "ps": u16(info + 34),
                "hist": [s16(info + 36), s16(info + 38)],
                "loop_ps": u16(info + 40),
                "loop_hist": [s16(info + 42), s16(info + 44)],
            })

    expected = ((blocks - 1) * block_size + last_padded) * channels if blocks else 0
    return {
        "version": "%d.%d" % (version >> 8, version & 0xFF),
        "byte_order": "big" if e == ">" else "little",
        "codec": CODECS.get(codec, "codec_%d" % codec),
        "channels": channels,
        "listed_channels": listed_channels,
        "sample_rate": rate,
        "loop": bool(loop),
        "loop_start": loop_start if loop else None,
        "total_samples": total,
        "duration": str(Fraction(total, rate)) if rate else None,
        "blocks": blocks,
        "block_samples": block_samples,
        "last_block_samples": last_samples,
        "tracks": tracks,
        "track_info": "short" if track_type == 1 else "long",
        "data_bytes": data_size - 0x20,
        "expected_data_bytes": expected,
        "truncated": data_off + data_size > len(data),
        "seek_interval": seek_interval,
        "adpcm": adpcm,
    }
