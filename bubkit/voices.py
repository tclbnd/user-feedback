"""Konami 005289 snapshots (.k5289): two wavetable channels addressed from a 4-bit PROM."""
import struct

CLOCK = 3579545
HDR = 8 + 11


class BubError(ValueError):
    pass


def mix(chs, prom, n=65536):
    """Mixed output of the two channels at the chip clock."""
    import zlib
    rd = lambda a: prom[a] if a < len(prom) else 0
    out = []
    for t in range(n):
        v = 0
        for i, c in enumerate(chs):
            pos = (t // (c["frequency"] + 1)) & 0x1F
            v += ((rd(i << 8 | c["waveform"] << 5 | pos) & 0x0F) - 8) * c["volume"]
        out.append(v)
    return {"rate_hz": CLOCK, "samples": n, "peak": max(abs(x) for x in out), "sum": sum(out),
            "crc32": "%08x" % zlib.crc32(b"".join(x.to_bytes(2, "little", signed=True) for x in out))}


def parse(data):
    if len(data) < 8 or data[:8] != b"K005289S":
        raise BubError("not a 005289 snapshot")
    if len(data) < HDR:
        raise BubError("register block is truncated")
    ld0, ld1, tg, ca, cb, o0, o1 = struct.unpack_from("<HHBBBHH", data, 8)
    prom = data[HDR:HDR + 512]
    out = []
    for ch, (ld, ctrl, old) in enumerate(((ld0, ca, o0), (ld1, cb, o1))):
        pitch = ld & 0xFFF
        fired = bool(tg >> ch & 1)
        freq = pitch
        wf = ctrl & 7
        base = wf << 5
        c = {"channel": ch + 1, "volume": ctrl >> 4, "waveform": wf, "prom_address": base,
             "frequency": freq, "pending": None,
             "cycle_hz": "%.3f" % (CLOCK / (32 * freq)) if freq else None, "wave_min": None, "wave_max": None, "wave_sum": None}
        if len(prom) >= base + 32:
            vals = list(prom[base:base + 32])
            c.update(wave_min=min(vals), wave_max=max(vals), wave_sum=sum(vals))
        out.append(c)
    return {"prom_complete": len(prom) == 512, "channels": out, "output": mix(out, prom)}
