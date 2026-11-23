"""FMOD sound banks (FSB5): header, sample header bit fields, extra chunks, name table, sample data."""
import struct
from fractions import Fraction

CODECS = {1: "pcm8", 2: "pcm16", 3: "pcm24", 4: "pcm32", 5: "pcmfloat", 6: "gcadpcm", 7: "imaadpcm", 8: "vag",
          9: "hevag", 10: "xma", 11: "mpeg", 12: "celt", 13: "at9", 14: "xwma", 15: "vorbis", 16: "fadpcm", 17: "opus"}
RATES = (4000, 8000, 11000, 11025, 16000, 22050, 24000, 32000, 44100, 48000, 96000)
CHANNELS = (1, 2, 6, 8)
EXTRAS = {1: "channels", 2: "frequency", 3: "loop", 4: "comment", 6: "xmaseek", 7: "dspcoeff", 9: "atrac9cfg",
          10: "xwmadata", 11: "vorbisdata", 13: "peakvolume", 14: "vorbisintralayers", 15: "opusdatasize"}
WIDTH = {"pcm8": 1, "pcm16": 2, "pcm24": 3, "pcm32": 4, "pcmfloat": 4}
CHUNK_NEXT, CHUNK_SIZE, CHUNK_KIND = 0x1, 0xFFFFFF << 1, 0x7F << 25
OFFSET_UNIT = 32


class FsbError(ValueError):
    pass


def parse(data):
    """Parse one FSB5 bank.

    Version 0 headers start at 0x40; version 1 dropped the old 4-byte padding
    field, so sample headers start at 0x3C (right after the codec word).
    Sample offsets are stored in 32-byte units. Extra chunks form a chain: the
    low bit of each chunk word says another chunk follows this one.
    """
    if data[:4] != b"FSB5" or len(data) < 0x3C:
        raise FsbError("not an FSB5 bank")
    version, count, shsize, ntsize, dsize, mode = struct.unpack_from("<6I", data, 4)
    base = 0x40 if version == 0 else 0x3C
    names_at = base + shsize
    data_at = names_at + ntsize
    if data_at > len(data):
        raise FsbError("bank header sizes run past the end of the file")
    p, samples = base, []
    for _ in range(count):
        if p + 8 > names_at:
            raise FsbError("sample headers run past the end of the header block")
        # bits 0..33: has-extras, rate index (4), channel code (2), offset (27);
        # bits 34..63: sample count (30). Channel code maps to 1/2/6/8 channels.
        mode_bits, = struct.unpack_from("<Q", data, p)
        p += 8
        rate_index = (mode_bits >> 1) & 0xF
        s = {"name": None, "sample_rate": RATES[rate_index] if rate_index < len(RATES) else None,
             "channels": CHANNELS[(mode_bits >> 5) & 3], "samples": mode_bits >> 34,
             "offset": ((mode_bits >> 7) & 0x7FFFFFF) * OFFSET_UNIT, "loop": None, "extras": [], "peak": None}
        if mode_bits & 1:
            while True:
                if p + 4 > names_at:
                    raise FsbError("extra chunk header runs past the end of the header block")
                word, = struct.unpack_from("<I", data, p)
                size, kind = (word & CHUNK_SIZE) >> 1, (word & CHUNK_KIND) >> 25
                p += 4
                if p + size > names_at:
                    raise FsbError("extra chunk body runs past the end of the header block")
                body = data[p:p + size]
                p += size
                if kind == 1 and size >= 1:
                    s["channels"] = body[0]
                elif kind == 2 and size >= 4:
                    s["sample_rate"], = struct.unpack_from("<I", body)
                elif kind == 3 and size >= 8:
                    start, end = struct.unpack_from("<II", body)
                    s["loop"] = {"start": start, "end": end}
                elif kind == 13 and size >= 4:
                    s["peak"] = "%.3f" % struct.unpack_from("<f", body)[0]
                s["extras"].append(EXTRAS.get(kind, "type_%d" % kind))
                if not word & CHUNK_NEXT:
                    break
        samples.append(s)
    if ntsize and count:
        names_end = names_at + ntsize
        if names_at + 4 * count > names_end:
            raise FsbError("name table is too small for its offsets")
        for i, s in enumerate(samples):
            off, = struct.unpack_from("<I", data, names_at + 4 * i)
            q = names_at + off
            stop = data.find(b"\0", q, names_end) if q < names_end else -1
            if q < names_at or stop < 0:
                raise FsbError("name table entry runs past the end of the name table")
            s["name"] = data[q:stop].decode("utf-8", "replace")
    codec = CODECS.get(mode, str(mode))
    # Each sample runs to the next sample's offset; the last one runs to the end
    # of the data section (offsets are relative to the start of that section).
    for i, s in enumerate(samples):
        end = samples[i + 1]["offset"] if i + 1 < count else dsize
        s["data_bytes"] = max(0, end - s["offset"])
        s["duration"] = str(Fraction(s["samples"], s["sample_rate"])) if s["sample_rate"] else None
        # Padding is measured against the sample's own (possibly overridden) channel count.
        s["padding_bytes"] = s["data_bytes"] - s["samples"] * s["channels"] * WIDTH[codec] if codec in WIDTH else None
        s["loop_ok"] = None if s["loop"] is None else 0 <= s["loop"]["start"] <= s["loop"]["end"] < s["samples"]
        del s["offset"]
    return {"version": version, "codec": codec, "sample_count": count, "data_bytes": dsize,
            "truncated": data_at + dsize > len(data), "samples": samples}
