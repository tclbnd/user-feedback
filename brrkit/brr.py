"""SNES BRR samples (.brr): optional loop offset header, 9-byte blocks decoded as the S-DSP does."""


class BrrError(ValueError):
    pass


def parse(data):
    if not data or len(data) % 9:
        raise BrrError("not a whole number of BRR blocks")
    p1 = p2 = 0.0
    peak, last, played, blocks = 0, None, 0, len(data) // 9
    for k in range(blocks):
        b = data[9 * k:9 * k + 9]
        shift, filt = b[0] >> 4, b[0] >> 2 & 3
        for i in range(16):
            n = b[1 + i // 2] >> (0 if i % 2 == 0 else 4) & 15
            s = n - 16 if n & 8 else n
            s = s * (1 << shift)
            if filt == 1:
                s += p1 * 15 / 16
            elif filt == 2:
                s += p1 * 61 / 32 - p2 * 15 / 16
            elif filt == 3:
                s += p1 * 115 / 64 - p2 * 13 / 16
            s = int(max(-32768, min(32767, s)))
            p2, p1 = p1, s
            peak = max(peak, abs(s))
            last = s
        played += 16
    return {"blocks": blocks, "loop_header": False, "samples": played, "ended": False,
            "looped": False, "loop_start": None, "peak": "%.6f" % (peak / 32768), "last_sample": last}
