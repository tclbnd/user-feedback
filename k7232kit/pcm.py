"""Konami K007232 snapshots (.k7232): two PCM channels, 12-bit step, 17-bit start, end bit and loop flags."""

CLOCK = 3579545
HDR = 8 + 14 + 1


class K7232Error(ValueError):
    pass


def parse(data):
    if len(data) < 8 or data[:8] != b"K007232S":
        raise K7232Error("not a K007232 snapshot")
    if len(data) < HDR:
        raise K7232Error("register block is truncated")
    r = data[8:22]
    keyed = data[22]
    rom = data[HDR:]
    chans = []
    for i in range(2):
        b = 6 * i
        step = r[b + 1] << 8 | r[b]
        start = r[b + 3] << 8 | r[b + 2]
        c = {"channel": i + 1, "keyed": bool(keyed >> i & 1), "step": step, "start": start,
             "rate_hz": "%.2f" % (CLOCK / (4096 - step)) if step < 4096 else None, "loop": bool(r[13] >> (1 - i) & 1),
             "length": None, "peak": None, "sum": None, "terminated": None}
        if c["keyed"]:
            vals, a = [], start
            while a < len(rom) and rom[a] != 0:
                v = rom[a]
                vals.append(v - 256 if v & 0x80 else v)
                a += 1
            c.update(length=len(vals), peak=max((abs(v) for v in vals), default=0), sum=sum(vals), terminated=a < len(rom))
        chans.append(c)
    return {"rom_size": len(rom), "channels": chans}
