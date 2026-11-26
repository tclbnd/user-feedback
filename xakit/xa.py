"""CD-XA ADPCM audio (.xa): 2336-byte Mode 2 Form 2 sectors, sound groups, sound units."""
from fractions import Fraction

K = [(0, 0), (60, 0), (115, -52), (98, -55)]
SECTOR = 2336


class XaError(ValueError):
    pass


def parse(data):
    if len(data) < SECTOR:
        raise XaError("not an XA file")
    streams, complete = {}, len(data) % SECTOR == 0
    for k in range(len(data) // SECTOR):
        s = data[k * SECTOR:(k + 1) * SECTOR]
        filenum, channel, submode, code = s[0:4]
        key = "%d/%d" % (filenum, channel)
        st = streams.setdefault(key, {"sectors": 0, "stereo": False, "rate": 37800, "bits": 4,
                                     "hist": [0, 0], "count": 0, "peak": 0, "last": None})
        st["sectors"] += 1
        for g in range(18):
            grp = s[8 + 128 * g:8 + 128 * (g + 1)]
            for u in range(8):
                p = grp[u]
                shift, filt = p & 15, p >> 4 & 3
                h = st["hist"]
                f0, f1 = K[filt]
                for i in range(28):
                    n = grp[16 + 4 * i + u // 2] >> (4 * (1 - (u & 1))) & 15
                    v = n - 16 if n & 8 else n
                    d = (v << 12) >> shift
                    y = max(-32768, min(32767, d + ((h[0] * f0 + h[1] * f1 + 32) >> 6)))
                    h[1], h[0] = h[0], y
                    st["peak"] = max(st["peak"], abs(y))
                    st["last"] = y
                    st["count"] += 1
    out = []
    for key, st in streams.items():
        n = st["count"]
        out.append({"stream": key, "sectors": st["sectors"], "stereo": False, "sample_rate": st["rate"],
                    "bits": 4, "samples": n, "duration": str(Fraction(n, st["rate"])),
                    "peak": "%.6f" % (st["peak"] / 32768), "last_left": st["last"], "last_right": None})
    return {"sectors": len(data) // SECTOR, "complete": complete, "streams": out}
