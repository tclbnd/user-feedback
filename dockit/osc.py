"""Ensoniq ES5503 DOC snapshots (.doc): 32 oscillators, wavetable size, resolution, modes and sound RAM."""

CLOCK = 7159090
HDR = 8 + 0xE3
MODES = ("free", "sync", "oneshot", "swap")


class DocError(ValueError):
    pass


def parse(data):
    if len(data) < 8 or data[:8] != b"ES5503DC":
        raise DocError("not an ES5503 snapshot")
    if len(data) < HDR:
        raise DocError("register block is truncated")
    r = data[8:HDR]
    ram = data[HDR:]
    n = min(32, r[0xE1] or 1)
    rate = CLOCK / n
    oscs = []
    for i in range(n):
        freq = r[0x00 + i] | r[0x20 + i] << 8
        ctrl, sz = r[0xA0 + i], r[0xC0 + i]
        size, res = sz >> 3 & 7, sz & 7
        base = r[0x80 + i] << 8
        length = 256 * (size + 1)
        o = {"osc": i + 1, "halted": bool(ctrl & 1), "mode": MODES[ctrl >> 1 & 3], "channel": ctrl >> 4,
             "volume": r[0x40 + i], "table_address": base, "table_size": length,
             "cycle_hz": "%.3f" % (rate * freq / (1 << 17)),
             "stops_at": None, "peak": None, "sum": None, "complete": base + length <= len(ram)}
        tbl = ram[base:base + length]
        vals = []
        for k, b in enumerate(tbl):
            if b == 0:
                o["stops_at"] = k
                break
            vals.append(b - 256 if b & 0x80 else b)
        o["peak"] = max((abs(v) for v in vals), default=0)
        o["sum"] = sum(vals)
        oscs.append(o)
    return {"oscillators": n, "output_rate": "%.2f" % rate, "oscs": oscs}
