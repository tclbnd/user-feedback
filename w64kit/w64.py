"""Sony Wave64 (.w64) container."""
import struct
from fractions import Fraction


class W64Error(ValueError):
    pass


def chunks(data):
    if data[:4] != b"riff" or data[24:28] != b"wave":
        raise W64Error("not a Wave64 file")
    pos, out = 40, []
    while pos + 24 <= len(data):
        name = data[pos:pos + 4].decode("latin-1").strip()
        size = struct.unpack_from("<Q", data, pos + 16)[0]
        out.append((name, data[pos + 24:pos + size]))
        pos += size
    return out


def summary(data):
    found = chunks(data)
    fmt = next((b for n, b in found if n == "fmt"), None)
    audio = next((b for n, b in found if n == "data"), None)
    if fmt is None or audio is None:
        raise W64Error("missing fmt or data chunk")
    tag, ch, rate, _avg, align, bits = struct.unpack_from("<HHIIHH", fmt)
    if tag not in (1, 0xFFFE):
        raise W64Error("unsupported format")
    width = bits // 8
    frames = len(audio) // (ch * width)
    peaks = [0.0] * ch
    for i in range(frames):
        for c in range(ch):
            p = (i * ch + c) * width
            v = int.from_bytes(audio[p:p + width], "little", signed=True)
            peaks[c] = max(peaks[c], abs(v) / 2 ** (bits - 1))
    return {
        "format": "pcm", "channels": ch, "sample_rate": rate, "bits": bits, "container_bits": bits,
        "block_align": align, "frames": frames, "duration": str(Fraction(frames, rate)),
        "peak": [round(p, 6) for p in peaks], "chunks": [n for n, _ in found],
    }
