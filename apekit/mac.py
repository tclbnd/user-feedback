"""Monkey's Audio (.ape) file headers.

Two layouts exist.  Encoder 3.98 and later write the APE descriptor, which
stores the stream size in 64 bits, the real sample size, and the seek table
size.  Every earlier version wrote the legacy single header: it has none of
those fields, so the sample size lives in the format flags, there is no stream
size or MD5, and the blocks per frame have to be reconstructed from the encoder
version that produced the file.
"""
import struct
from fractions import Fraction

LEVELS = {1000: "fast", 2000: "normal", 3000: "high", 4000: "extra high", 5000: "insane"}
FLAG_8BIT, FLAG_CRC, FLAG_PEAK, FLAG_24BIT, FLAG_SEEK, FLAG_NO_WAV = 1, 2, 4, 8, 16, 32
# FLAG_NO_WAV says the WAV header is rebuilt on decode instead of being stored,
# so there are no header bytes in the file when it is set.

# 3.98 is the first version that writes the APE descriptor.
DESCRIPTOR_VERSION = 3980


class MacError(ValueError):
    pass


def _id3v2_size(data, pos):
    """Total bytes of the ID3v2 tag at *pos* (0 when there is none)."""
    if data[pos:pos + 3] != b"ID3" or len(data) < pos + 10:
        return 0
    size = 0
    for byte in data[pos + 6:pos + 10]:          # syncsafe integer
        size = (size << 7) | (byte & 0x7F)
    total = 10 + size
    if data[pos + 3] == 4 and data[pos + 5] & 0x10:   # ID3v2.4 tag footer
        total += 10
    return total


def _legacy_bits(flags):
    return 8 if flags & FLAG_8BIT else 24 if flags & FLAG_24BIT else 16


def _legacy_blocks_per_frame(version, level):
    """Blocks per frame used by the old encoders, by version.

    The legacy header has no field for this, so the value that version's
    encoder always used is reconstructed instead.
    """
    if version >= 3950:
        return 73728 * 4
    if version >= 3900 or (version >= 3800 and level >= 4000):
        return 73728
    return 9216


def parse(data):
    start = 0
    while True:                                  # skip ID3v2 tags in front of the stream
        tag = _id3v2_size(data, start)
        if not tag:
            break
        start += tag
    if data[start:start + 4] != b"MAC ":
        raise MacError("not a Monkey's Audio file")
    version, = struct.unpack_from("<H", data, start + 4)
    out = {"id3v2_bytes": start,
           "version": "%d.%02d" % (version // 1000, version % 1000 // 10)}
    if version >= DESCRIPTOR_VERSION:
        (_, _, desc_bytes, _header_bytes, seek_bytes, wav_bytes,
         data_lo, data_hi, _wav_tail) = struct.unpack_from("<HHIIIIIII", data, start + 4)
        md5 = data[start + 36:start + 52].hex()
        level, flags, bpf, final, frames, sample_bits, ch, rate = \
            struct.unpack_from("<HHIIIHHI", data, start + desc_bytes)
        # The descriptor carries the real sample size; the 8/24-bit format
        # flags belong to the legacy header and are not set here.
        bits = sample_bits or _legacy_bits(flags)
        out.update(layout="descriptor", seek_entries=seek_bytes // 4,
                   wav_header_bytes=0 if flags & FLAG_NO_WAV else wav_bytes,
                   # The stream size is 64 bit: the high word holds everything past 4 GiB.
                   frame_data_bytes=data_lo | (data_hi << 32), md5=md5, peak_level=None)
    else:
        (_, level, flags, ch, rate, wav_bytes, _wav_tail, frames, final) = \
            struct.unpack_from("<HHHHIIIII", data, start + 4)
        pos = start + 32
        peak_level = None
        if flags & FLAG_PEAK:
            peak_level, = struct.unpack_from("<I", data, pos)
            pos += 4
        if flags & FLAG_SEEK:
            seek_entries, = struct.unpack_from("<I", data, pos)
        else:
            seek_entries = frames                # the table is one entry per frame
        bits = _legacy_bits(flags)
        out.update(layout="old", seek_entries=seek_entries,
                   wav_header_bytes=0 if flags & FLAG_NO_WAV else wav_bytes,
                   frame_data_bytes=None, md5=None, peak_level=peak_level)
        bpf = _legacy_blocks_per_frame(version, level)
    # Every frame but the last is full; the last holds only `final` blocks, so
    # counting it as a whole frame would make every track too long.
    blocks = (frames - 1) * bpf + final if frames else 0
    out.update(compression=LEVELS.get(level, str(level)), channels=ch, bits=bits,
               sample_rate=rate, blocks_per_frame=bpf, total_frames=frames,
               final_frame_blocks=final, total_blocks=blocks,
               duration=str(Fraction(blocks, rate)) if rate else None)
    return out
