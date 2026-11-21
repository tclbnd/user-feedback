"""LOAS/LATM stream builder."""
RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350)


class W:
    def __init__(self):
        self.bits = []

    def put(self, v, n):
        for i in range(n - 1, -1, -1):
            self.bits.append((v >> i) & 1)
        return self

    def extend(self, other):
        self.bits += other.bits
        return self

    def data(self):
        b = self.bits + [0] * (-len(self.bits) % 8)
        return bytes(int("".join(map(str, b[i:i + 8])), 2) for i in range(0, len(b), 8))


def _rate(w, rate):
    if rate in RATES:
        w.put(RATES.index(rate), 4)
    else:
        w.put(15, 4).put(rate, 24)


def _aot(w, a):
    if a < 31:
        w.put(a, 5)
    else:
        w.put(31, 5).put(a - 32, 6)


def asc(aot=2, rate=48000, chconf=2, frame960=False, sbr=None, sbr_aot=5):
    w = W()
    if sbr:
        _aot(w, sbr_aot)
        _rate(w, rate)
        w.put(chconf, 4)
        _rate(w, sbr)
        _aot(w, aot)
    else:
        _aot(w, aot)
        _rate(w, rate)
        w.put(chconf, 4)
    w.put(1 if frame960 else 0, 1).put(0, 1).put(0, 1)
    return w


def latm_value(w, v):
    n = max(1, (v.bit_length() + 7) // 8)
    w.put(n - 1, 2).put(v, 8 * n)


def smc(cfg, version=0, subframes=1, other=None, crc=None, asc_pad=0):
    w = W().put(version, 1)
    if version == 1:
        w.put(0, 1)
        latm_value(w, 0xFF)
    w.put(1, 1).put(subframes - 1, 6).put(0, 4).put(0, 3)
    if version == 0:
        w.extend(cfg)
    else:
        latm_value(w, len(cfg.bits) + asc_pad)
        w.extend(cfg).put(0, asc_pad)
    w.put(0, 3).put(0xFF, 8)
    if other is None:
        w.put(0, 1)
    else:
        w.put(1, 1)
        if version == 1:
            latm_value(w, other)
        else:
            v = other
            chunks = []
            while True:
                chunks.append(v & 0xFF)
                v >>= 8
                if not v:
                    break
            chunks.reverse()
            for i, c in enumerate(chunks):
                w.put(1 if i < len(chunks) - 1 else 0, 1).put(c, 8)
    if crc is None:
        w.put(0, 1)
    else:
        w.put(1, 1).put(crc, 8)
    return w


def au(n, seed=0):
    return bytes(((i * 37 + seed * 11) & 0xFF) for i in range(n))


def frame(payloads, config=None):
    w = W()
    if config is None:
        w.put(1, 1)
    else:
        w.put(0, 1).extend(config)
    for p in payloads:
        n = len(p)
        while n >= 255:
            w.put(255, 8)
            n -= 255
        w.put(n, 8)
        for b in p:
            w.put(b, 8)
    body = w.data()
    return bytes([0x56, 0xE0 | (len(body) >> 8), len(body) & 0xFF]) + body


def stream(config, count=4, size=100, every=0, subframes=1):
    out = b""
    for i in range(count):
        cfg = config if i == 0 or (every and i % every == 0) else None
        out += frame([au(size + k, i) for k in range(subframes)], cfg)
    return out
