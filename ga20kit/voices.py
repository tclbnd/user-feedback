"""Irem GA20 snapshots (.ga20): four PCM voices, start/end registers, rate, volume and the sample ROM."""

CLOCK = 3579545
HDR = 8 + 32


class Ga20Error(ValueError):
    pass


def parse(data):
    if len(data) < 8 or data[:8] != b"GA20SNAP":
        raise Ga20Error("not a GA20 snapshot")
    if len(data) < HDR:
        raise Ga20Error("register block is truncated")
    regs = data[8:HDR]
    rom = data[HDR:]
    voices = []
    for ch in range(4):
        r = regs[8 * ch:8 * ch + 8]
        start = r[0] | r[1] << 8
        end = r[2] | r[3] << 8
        rate = r[4]
        vol = r[5]
        on = bool(r[6] & 1)
        v = {"voice": ch + 1, "on": on, "start": start, "end": end, "volume": vol,
             "rate_hz": "%.2f" % (CLOCK / (256 - rate)) if rate < 256 else None,
             "length": None, "peak": None, "sum": None, "terminated": False}
        if on:
            vals = []
            for a in range(start, min(end, len(rom))):
                b = rom[a]
                vals.append(b - 256 if b & 0x80 else b)
            v["terminated"] = end <= len(rom)
            v["length"] = len(vals)
            v["peak"] = max((abs(x) for x in vals), default=0)
            v["sum"] = sum(vals)
        voices.append(v)
    return {"rom_size": len(rom), "voices_on": sum(v["on"] for v in voices), "voices": voices}
