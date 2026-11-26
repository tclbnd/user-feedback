"""PlayStation SPU ADPCM sounds (.vag): header, 16-byte frames, loop flags."""
import struct
from fractions import Fraction

COEF = [(0, 0), (60, 0), (115, -52)]


class VagError(ValueError):
    pass


def parse(data):
    if len(data) < 48 or data[:4] != b"VAGp":
        raise VagError("not a VAG file")
    version, _, size, rate = struct.unpack_from("<IIII", data, 4)
    name = data[32:48].split(b"\0")[0].decode("latin-1")
    body = data[48:]
    nframes = len(body) // 16
    h1 = h2 = 0
    peak, last, played = 0, None, 0
    for k in range(nframes):
        fr = body[16 * k:16 * k + 16]
        shift, filt = fr[0] & 15, fr[0] >> 4
        if filt >= len(COEF):
            raise VagError("bad filter %d" % filt)
        f0, f1 = COEF[filt]
        for i in range(28):
            nib = fr[2 + i // 2] >> (4 * (1 - i % 2)) & 15
            d = ((nib - 16 if nib & 8 else nib) << 12) >> shift
            y = max(-32768, min(32767, d + ((h1 * f0 + h2 * f1 + 32) >> 6)))
            h2, h1 = h1, y
            peak = max(peak, abs(y))
            last = y
        played += 28
    return {"name": name, "version": version, "sample_rate": rate, "frames": nframes, "samples": played,
            "duration": str(Fraction(played, rate)) if rate else None, "ended": False, "looped": False,
            "loop_start": None, "peak": "%.6f" % (peak / 32768), "last_sample": last}
