"""Nintendo BRSTM streams: header, HEAD chunk references, stream / track / channel info, blocks."""
import struct
from fractions import Fraction

CODECS = {0: "pcm8", 1: "pcm16", 2: "dsp_adpcm"}


class RstmError(ValueError):
    pass


def parse(data):
    if data[:4] != b"RSTM":
        raise RstmError("not a BRSTM stream")
    bom = data[4:6]
    e = ">" if bom == b"\xfe\xff" else "<" if bom == b"\xff\xfe" else None
    if e is None:
        raise RstmError("bad byte order mark")
    u8 = lambda p: data[p]
    u16 = lambda p: struct.unpack_from(e + "H", data, p)[0]
    u32 = lambda p: struct.unpack_from(e + "I", data, p)[0]
    version = u16(6)
    head_off, head_size = u32(0x10), u32(0x14)
    data_off, data_size = u32(0x20), u32(0x24)
    if data[head_off:head_off + 4] != b"HEAD":
        raise RstmError("HEAD chunk not found")
    # Reference offsets are relative to the start of HEAD's data,
    # immediately after the "HEAD" magic and chunk size.
    base = head_off + 8
    refs = [u32(head_off + 8 + 8 * i + 4) for i in range(3)]
    s = base + refs[0]
    codec, loop, channels = u8(s), u8(s + 1), u8(s + 2)
    rate = u16(s + 4)
    loop_start, total = u32(s + 8), u32(s + 12)
    if not loop:
        loop_start = None
    blocks, block_size, block_samples = u32(s + 20), u32(s + 24), u32(s + 28)
    last_size, last_samples, last_padded = u32(s + 32), u32(s + 36), u32(s + 40)
    t = base + refs[1]
    tracks, track_type = u8(t), u8(t + 1)
    c = base + refs[2]
    listed_channels = u8(c)
    expected = ((blocks - 1) * block_size + last_padded) * channels if blocks else 0
    duration = str(Fraction(total, rate)) if rate else None
    return {"version": "%d.%d" % (version >> 8, version & 0xFF), "byte_order": "big" if e == ">" else "little",
            "codec": CODECS.get(codec, "codec_%d" % codec), "channels": channels, "listed_channels": listed_channels,
            "sample_rate": rate, "loop": bool(loop), "loop_start": loop_start,
            "total_samples": total, "duration": duration,
            "blocks": blocks, "block_samples": block_samples, "last_block_samples": last_samples,
            "tracks": tracks, "track_info": "long" if track_type else "short",
            "data_bytes": data_size - 0x20, "expected_data_bytes": expected,
            "truncated": data_off + data_size > len(data)}
