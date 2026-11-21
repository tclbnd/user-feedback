"""Creative Voice File (.voc) blocks."""
import struct
from fractions import Fraction

MAGIC = b"Creative Voice File\x1a"
CODECS = {0: "pcm_u8", 1: "adpcm4", 2: "adpcm26", 3: "adpcm2", 4: "pcm_s16", 6: "alaw", 7: "ulaw"}


class VocError(ValueError):
    pass


def blocks(data):
    if data[:20] != MAGIC:
        raise VocError("not a Creative Voice File")
    version = struct.unpack_from("<H", data, 22)[0]
    pos, out = 26, []
    while pos < len(data):
        kind = data[pos]
        if kind == 0:
            break
        length = int.from_bytes(data[pos + 1:pos + 4], "little")
        out.append((kind, data[pos + 4:pos + 4 + length]))
        pos += 4 + length
    return version, out


def scan(data):
    version, found = blocks(data)
    total = Fraction(0)
    audio = bytearray()
    counts = {}
    first = None
    for kind, body in found:
        counts[kind] = counts.get(kind, 0) + 1
        if kind == 1:
            tc, pack = body[0], body[1]
            rate = Fraction(1000000, 256 - tc)
            if first is None:
                first = (rate, 1, CODECS.get(pack, "unknown"))
            total += Fraction(len(body) - 2) / rate
            audio += body[2:]
        elif kind == 3:
            n, tc = struct.unpack_from("<HB", body)
            total += Fraction(n) / Fraction(1000000, 256 - tc)
        elif kind not in (2, 4, 5, 6, 7):
            raise VocError("unsupported block type %d" % kind)
    peak = max((abs(b - 256 if b > 127 else b) for b in audio), default=0) if audio else None
    return {
        "version": "%d.%02d" % (version >> 8, version & 0xFF),
        "blocks": {str(k): v for k, v in sorted(counts.items())},
        "sample_rate": None if first is None else float(round(first[0], 3)),
        "channels": None if first is None else first[1],
        "codec": None if first is None else first[2],
        "duration": str(total),
        "peak": peak,
        "markers": [],
        "texts": [],
    }
