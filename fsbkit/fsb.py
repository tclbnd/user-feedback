"""FMOD sound banks (FSB5): header, sample header bit fields, extra chunks, name table, sample data."""
import struct
from fractions import Fraction

CODECS = {1: "pcm8", 2: "pcm16", 3: "pcm24", 4: "pcm32", 5: "pcmfloat", 6: "gcadpcm", 7: "imaadpcm", 8: "vag",
          9: "hevag", 10: "xma", 11: "mpeg", 12: "celt", 13: "at9", 14: "xwma", 15: "vorbis", 16: "fadpcm", 17: "opus"}
RATES = (8000, 11000, 11025, 16000, 22050, 24000, 32000, 44100, 48000, 96000)
CHANNELS = (1, 2, 3, 4)


class FsbError(ValueError):
    pass


def parse(data):
    if data[:4] != b"FSB5" or len(data) < 0x3C:
        raise FsbError("not an FSB5 bank")
    version, count, shsize, ntsize, dsize, mode = struct.unpack_from("<6I", data, 4)
    base = 0x40
    names_at = base + shsize
    data_at = names_at + ntsize
    if data_at > len(data):
        raise FsbError("bank header sizes run past the end of the file")
    p, samples = base, []
    for _ in range(count):
        mode_bits, = struct.unpack_from("<Q", data, p)
        p += 8
        s = {"name": None, "sample_rate": RATES[(mode_bits >> 1) & 0xF] if (mode_bits >> 1) & 0xF < len(RATES) else None,
             "channels": CHANNELS[(mode_bits >> 5) & 3], "samples": (mode_bits >> 34) & 0xFFFFFF,
             "offset": (mode_bits >> 7) & 0x7FFFFFF, "loop": None, "extras": []}
        if mode_bits & 1:
            word, = struct.unpack_from("<I", data, p)
            size, kind = (word >> 1) & 0xFFFFFF, word >> 25
            body = data[p + 4:p + 4 + size]
            p += 4 + size
            if kind == 1:
                s["channels"] = body[0]
            elif kind == 2:
                s["sample_rate"], = struct.unpack_from("<I", body)
            elif kind == 3:
                start, end = struct.unpack_from("<II", body)
                s["loop"] = {"start": start, "end": end}
            s["extras"].append(kind)
        samples.append(s)
    if ntsize and count:
        for i, s in enumerate(samples):
            off, = struct.unpack_from("<I", data, names_at + 4 * i)
            q = names_at + off
            s["name"] = data[q:data.index(b"\0", q)].decode("utf-8", "replace")
    for i, s in enumerate(samples):
        end = samples[i + 1]["offset"] if i + 1 < count else len(data) - data_at
        s["data_bytes"] = end - s["offset"]
        s["duration"] = str(Fraction(s["samples"], s["sample_rate"])) if s["sample_rate"] else None
        del s["offset"]
    return {"version": version, "codec": CODECS.get(mode, str(mode)), "sample_count": count, "data_bytes": dsize,
            "truncated": data_at + dsize > len(data), "samples": samples}
