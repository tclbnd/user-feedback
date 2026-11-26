"""Nintendo DS SWAV sounds (.swav): file header, DATA block, PCM8/PCM16/IMA-ADPCM waves."""
import struct
from fractions import Fraction

KINDS = {0: "pcm8", 1: "pcm16", 2: "adpcm"}


class SwavError(ValueError):
    pass


def parse(data):
    if len(data) < 16 + 8 + 12 or data[:4] != b"SWAV":
        raise SwavError("not a SWAV file")
    b = 16
    if data[b:b + 4] != b"DATA":
        raise SwavError("missing DATA block")
    kind, loop, rate, timer, loff, nlen = struct.unpack_from("<BBHHHI", data, b + 8)
    if kind not in (0, 1):
        raise SwavError("unsupported wave type %d" % kind)
    payload = data[b + 20:]
    nbytes = loff + nlen
    complete = len(payload) >= nbytes
    payload = payload[:nbytes]
    if kind == 0:
        total, lstart = nbytes, loff
        vals = [(v - 128) << 8 for v in payload]
    else:
        total, lstart = nbytes // 2, loff // 2
        vals = [struct.unpack_from("<h", payload, 2 * i)[0] for i in range(len(payload) // 2)]
    peak = max((abs(v) for v in vals), default=0)
    return {"type": KINDS[kind], "looped": bool(loop), "sample_rate": rate, "timer": timer,
            "timer_rate": None, "samples": total,
            "loop_start": lstart if loop else None, "duration": str(Fraction(total, rate)) if rate else None,
            "complete": complete, "peak": "%.6f" % (peak / 32768), "last_sample": vals[-1] if vals else None}
