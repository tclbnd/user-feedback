"""FMOD sound banks (FSB5): header, sample header bit fields, extra chunks, name table, sample data."""
import struct
from fractions import Fraction

CODECS = {1: "pcm8", 2: "pcm16", 3: "pcm24", 4: "pcm32", 5: "pcmfloat", 6: "gcadpcm", 7: "imaadpcm", 8: "vag",
          9: "hevag", 10: "xma", 11: "mpeg", 12: "celt", 13: "at9", 14: "xwma", 15: "vorbis", 16: "fadpcm", 17: "opus"}
# Rate index stored in bits 1-4 of a sample's mode bits. Index 0 is the 4 kHz slot;
# leaving it out shifts every later index by one, so 44.1 kHz reads back as 48 kHz.
RATES = (4000, 8000, 11000, 11025, 16000, 22050, 24000, 32000, 44100, 48000, 96000)

# Channel code stored in bits 5-6: mono, stereo, 5.1, 7.1.
CHANNELS = (1, 2, 6, 8)

# Sample data offsets are stored in 32-byte units.
DATA_ALIGN = 32

# Extra chunk ids that override or add to the sample header fields.
CHUNK_CHANNELS = 1
CHUNK_SAMPLE_RATE = 2
CHUNK_LOOP = 3

# Shortest bank header; version 0 banks carry one more 4-byte field before the sample headers.
HEADER_SIZE = 0x3C


class FsbError(ValueError):
    pass


def header_size(version):
    """Offset of the first sample header: version 0 banks are 4 bytes longer than version 1."""
    return 0x40 if version == 0 else HEADER_SIZE


def read_sample(data, p, limit):
    """Decode one sample header and its chain of extra chunks; return the sample and the next offset."""
    if p + 8 > limit:
        raise FsbError("sample header runs past the sample header section")
    mode_bits, = struct.unpack_from("<Q", data, p)
    p += 8
    rate_index = (mode_bits >> 1) & 0xF
    s = {"name": None,
         "sample_rate": RATES[rate_index] if rate_index < len(RATES) else None,
         "channels": CHANNELS[(mode_bits >> 5) & 0x3],
         "samples": mode_bits >> 34,
         "offset": ((mode_bits >> 7) & 0x7FFFFFF) * DATA_ALIGN,
         "loop": None, "extras": []}
    more = mode_bits & 1
    while more:
        if p + 4 > limit:
            raise FsbError("extra chunk runs past the sample header section")
        word, = struct.unpack_from("<I", data, p)
        p += 4
        size, kind = (word >> 1) & 0xFFFFFF, word >> 25
        if p + size > limit:
            raise FsbError("extra chunk runs past the sample header section")
        body = data[p:p + size]
        p += size
        if kind == CHUNK_CHANNELS and size >= 1:
            s["channels"] = body[0]
        elif kind == CHUNK_SAMPLE_RATE and size >= 4:
            s["sample_rate"], = struct.unpack_from("<I", body)
        elif kind == CHUNK_LOOP and size >= 8:
            start, end = struct.unpack_from("<II", body)
            s["loop"] = {"start": start, "end": end}
        s["extras"].append(kind)
        more = word & 1
    return s, p


def parse(data):
    if data[:4] != b"FSB5" or len(data) < HEADER_SIZE:
        raise FsbError("not an FSB5 bank")
    version, count, shsize, ntsize, dsize, mode = struct.unpack_from("<6I", data, 4)
    base = header_size(version)
    names_at = base + shsize
    data_at = names_at + ntsize
    if names_at > len(data) or data_at > len(data):
        raise FsbError("bank header sizes run past the end of the file")
    p, samples = base, []
    for _ in range(count):
        s, p = read_sample(data, p, names_at)
        samples.append(s)
    if ntsize and count:
        names_end = names_at + ntsize
        for i, s in enumerate(samples):
            if names_at + 4 * i + 4 > names_end:
                break
            off, = struct.unpack_from("<I", data, names_at + 4 * i)
            q = names_at + off
            if not names_at <= q < names_end:
                continue
            end = data.find(b"\0", q, names_end)
            s["name"] = data[q:end if end >= 0 else names_end].decode("utf-8", "replace")
    for i, s in enumerate(samples):
        end = samples[i + 1]["offset"] if i + 1 < count else dsize
        s["data_bytes"] = max(0, end - s["offset"])
        s["duration"] = str(Fraction(s["samples"], s["sample_rate"])) if s["sample_rate"] else None
        del s["offset"]
    return {"version": version, "codec": CODECS.get(mode, str(mode)), "sample_count": count, "data_bytes": dsize,
            "truncated": data_at + dsize > len(data), "samples": samples}
