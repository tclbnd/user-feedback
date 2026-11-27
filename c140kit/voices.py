"""Namco C140 snapshots (.c140): 24 voices, banked 16-bit sample ROM, linear and compressed samples."""

CLOCK = 21390
HDR = 8 + 24 * 16
BANK_WORDS = 1 << 16
RATE_DIV = 1 << 15


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


def _s12(w):
    # linear samples are 12-bit signed, held in the low 12 bits of a word
    w &= 0xfff
    return w - 0x1000 if w & 0x800 else w


def _be16(rom, word):
    off = 2 * word
    return (rom[off] << 8) | rom[off + 1]


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
        # registers are big-endian 16-bit words
        mode = (r[4] << 8) | r[5]
        if not mode & 0x80:
            continue
        freq = (r[2] << 8) | r[3]
        start = (r[6] << 8) | r[7]
        end = (r[8] << 8) | r[9]
        loop = (r[10] << 8) | r[11]
        # bank select is the upper byte of the control word; each bank is 64K words
        base = (mode >> 8) * BANK_WORDS
        comp = bool(mode & 0x08)
        v = {"voice": i + 1, "address": base + start, "length": end - start, "loop_start": base + loop if mode & 0x10 else None,
             "format": "compressed" if comp else "linear", "left": r[1], "right": r[0],
             "rate_hz": "%.2f" % (CLOCK * freq / RATE_DIV), "peak": None, "sum": None, "complete": True}
        vals = []
        for k in range(max(end - start, 0)):
            a = base + start + k
            if a >= nwords:
                v["complete"] = False
                break
            w = _be16(rom, a)
            # compressed samples live in the high byte; linear ones are signed 12-bit
            vals.append(TABLE[w >> 8] if comp else _s12(w))
        v["peak"] = max((abs(x) for x in vals), default=0)
        v["sum"] = sum(vals)
        voices.append(v)
    return {"rom_words": nwords, "voices_keyed": len(voices), "voices": voices}
