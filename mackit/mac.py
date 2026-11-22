"""Monkey's Audio (.ape) file headers.

Two on-disk layouts are handled:

* ``descriptor`` - the 3.98+ layout: a 52-byte descriptor, the 24-byte header,
  the seek table and then the stored original WAV header.
* ``old`` - the single header written by encoders before 3.98: fixed fields,
  an optional peak level and seek count, the stored WAV header, then the seek
  table.  The number of blocks per frame was never stored, so it is derived
  from the encoder version and compression level.

Both may be preceded by ID3v2 tags, which are skipped.  Seek-table offsets are
relative to the ``MAC `` header, so the ID3v2 length is added back when
reporting file offsets.
"""
import struct
from fractions import Fraction

LEVELS = {1000: "fast", 2000: "normal", 3000: "high", 4000: "extra high", 5000: "insane"}
FLAG_8BIT, FLAG_CRC, FLAG_PEAK, FLAG_24BIT, FLAG_SEEK, FLAG_NO_WAV = 1, 2, 4, 8, 16, 32

DESCRIPTOR_VERSION = 3980


class MacError(ValueError):
    pass


def _id3v2_bytes(data):
    """Total length of any leading ID3v2 tags (headers included), 0 if none."""
    total = 0
    while data[total:total + 3] == b"ID3":
        if len(data) < total + 10:
            raise MacError("truncated ID3v2 tag")
        flags = data[total + 5]
        size = ((data[total + 6] & 0x7F) << 21 | (data[total + 7] & 0x7F) << 14 |
                (data[total + 8] & 0x7F) << 7 | (data[total + 9] & 0x7F))
        footer = 10 if data[total + 3] >= 4 and flags & 0x10 else 0
        total += 10 + size + footer
        if total > len(data):
            raise MacError("truncated ID3v2 tag")
    return total


def _bits_from_flags(flags):
    if flags & FLAG_8BIT:
        return 8
    if flags & FLAG_24BIT:
        return 24
    return 16


def _old_blocks_per_frame(version, level):
    """Blocks per frame the pre-3.98 encoders used (the header omits it).

    3.95+ always use the quadruple frame; 3.90-3.94 use the standard frame;
    3.80-3.89 use it for "extra high" and "insane"; older versions always used
    the small frame.  This is the encoders' own rule.
    """
    if version >= 3950:
        return 73728 * 4
    if version >= 3900 or (version >= 3800 and level >= 4000):
        return 73728
    return 9216


def _total_blocks(frames, bpf, final):
    # Every frame is full except the last, which holds `final` blocks.
    if not frames:
        return 0
    return (frames - 1) * bpf + final


def _seek_table(entries, size, id3):
    # Entries are relative to the MAC header; add the ID3v2 length for a file offset.
    offsets = [id3 + e for e in entries]
    return {
        "first_frame_offset": offsets[0] if offsets else None,
        "seek_table_ok": all(0 <= o < size for o in offsets)
                         and all(a < b for a, b in zip(offsets, offsets[1:])),
    }


def parse(data):
    id3 = _id3v2_bytes(data)
    start = id3
    if data[start:start + 4] != b"MAC ":
        raise MacError("not a Monkey's Audio file")
    version, = struct.unpack_from("<H", data, start + 4)
    out = {"id3v2_bytes": id3,
           "version": "%d.%02d" % (version // 1000, version % 1000 // 10)}
    if version >= DESCRIPTOR_VERSION:
        out.update(_parse_descriptor(data, start, version, id3))
    else:
        out.update(_parse_old(data, start, version, id3))
    return out


def _parse_descriptor(data, start, version, id3):
    (_, _, desc_bytes, header_bytes, seek_bytes, wav_bytes,
     frame_lo, frame_hi, _term) = struct.unpack_from("<HHIIIIIII", data, start + 4)
    md5 = data[start + 36:start + 52].hex()
    h = start + desc_bytes
    level, flags, bpf, final, frames, depth, ch, rate = struct.unpack_from("<HHIIIHHI", data, h)
    bits = depth or _bits_from_flags(flags)
    n = seek_bytes // 4
    at = h + header_bytes
    entries = struct.unpack_from("<%dI" % n, data, at) if n else ()
    total = _total_blocks(frames, bpf, final)
    out = {
        "layout": "descriptor", "compression": LEVELS.get(level, str(level)),
        "channels": ch, "bits": bits, "sample_rate": rate,
        "blocks_per_frame": bpf, "total_frames": frames, "final_frame_blocks": final,
        "total_blocks": total,
        "duration": str(Fraction(total, rate)) if rate else None,
        "seek_entries": n, "wav_header_bytes": wav_bytes,
        "frame_data_bytes": frame_lo | (frame_hi << 32), "md5": md5,
        "peak_level": None,
    }
    out.update(_seek_table(entries, len(data), id3))
    return out


def _parse_old(data, start, version, id3):
    # Every pre-3.98 header has the same fixed fields at offset 6 (right after
    # the uint16 version): level, flags, channels, rate, stored-WAV size,
    # terminating-WAV size, frame count and blocks in the final frame.
    level, flags, ch, rate, wav_bytes, _wav_tail, frames, final = \
        struct.unpack_from("<HHHIIIII", data, start + 6)
    at = start + 32
    peak = None
    if flags & FLAG_PEAK:
        peak, = struct.unpack_from("<I", data, at)
        at += 4
    if flags & FLAG_SEEK:
        n, = struct.unpack_from("<I", data, at)
        at += 4
    else:
        n = frames
    # The original WAV header is stored here, before the seek table.
    at += wav_bytes
    entries = struct.unpack_from("<%dI" % n, data, at) if n else ()
    bpf = _old_blocks_per_frame(version, level)
    total = _total_blocks(frames, bpf, final)
    out = {
        "layout": "old", "compression": LEVELS.get(level, str(level)),
        "channels": ch, "bits": _bits_from_flags(flags), "sample_rate": rate,
        "blocks_per_frame": bpf, "total_frames": frames, "final_frame_blocks": final,
        "total_blocks": total,
        "duration": str(Fraction(total, rate)) if rate else None,
        "seek_entries": n, "wav_header_bytes": wav_bytes,
        "frame_data_bytes": None, "md5": None, "peak_level": peak,
    }
    out.update(_seek_table(entries, len(data), id3))
    return out
