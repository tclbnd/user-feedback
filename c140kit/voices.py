"""Namco C140 snapshots (.c140): 24 voices, banked 16-bit sample ROM, linear and compressed samples."""

CLOCK = 21390
HDR = 8 + 24 * 16


class C140Error(ValueError):
    pass


def _table():
    # 8-bit companded sample -> 16-bit value
    t = []
    for i in range(256):
        sign, exp, man = i & 0x80, (i >> 4) & 7, i & 15
        v = ((man << 3) + 0x84) << exp
        t.append(-v if sign else v)
    return t


TABLE = _table()


def _s16(w):
    return w - 0x10000 if w & 0x8000 else w


def parse(data):
    if len(data) < 8 or data[:8] != b"C140SNAP":
        raise C140Error("not a C140 snapshot")
    if len(data) < HDR:
        raise C140Error("register block is truncated")
    regs = data[8:HDR]
    rom = data[HDR:]
    nwords = len(rom) // 2
    voices = []
    for i in range(24):
        r = regs[16 * i:16 * i + 16]
        mode = r[5]
        if not mode & 0x80:
            continue
        freq = r[2] << 8 | r[3]
        start, end, loop = r[7] << 8 | r[6], r[9] << 8 | r[8], r[11] << 8 | r[10]
        base = r[4] << 8
        comp = bool(mode & 0x08)
        v = {"voice": i + 1, "address": base + start, "length": end - start, "loop_start": base + loop if mode & 0x10 else None,
             "format": "compressed" if comp else "linear", "left": r[1], "right": r[0],
             "rate_hz": "%.2f" % (CLOCK * freq / 65536), "peak": None, "sum": None, "complete": True}
        vals = []
        for k in range(max(end - start, 0)):
            a = base + start + k
            if a >= nwords:
                v["complete"] = False
                break
            w = rom[2 * a] | rom[2 * a + 1] << 8
            vals.append(TABLE[w >> 8] if comp else _s16(w))
        v["peak"] = max((abs(x) for x in vals), default=0)
        v["sum"] = sum(vals)
        voices.append(v)
    return {"rom_words": nwords, "voices_keyed": len(voices), "voices": voices}
