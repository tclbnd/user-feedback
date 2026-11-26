"""NES DMC channel snapshots (.ndmc): sample address, length, rate and the delta-modulated output level."""

NTSC = [398, 354, 316, 298, 276, 236, 210, 198, 176, 148, 132, 118, 98, 78, 66, 50]
CPU = {0: 1789773, 1: 1662607}


class DmcError(ValueError):
    pass


def parse(data):
    if len(data) < 11 or data[:6] != b"NESDMC":
        raise DmcError("not a DMC snapshot")
    region = data[6]
    if region not in CPU:
        raise DmcError("unknown region %d" % region)
    r0, r1, r2, r3 = data[7:11]
    prg = data[11:11 + 0x8000]
    complete = len(prg) == 0x8000
    period = NTSC[r0 & 15]
    start = 0xC000 + r2 * 16
    length = r3 * 16
    level = r1
    lo = hi = level
    changes = 0
    played = 0
    for i in range(length):
        a = start + i
        if a - 0x8000 >= len(prg):
            break
        b = prg[a - 0x8000]
        for k in range(7, -1, -1):
            level += 2 if b >> k & 1 else -2
            changes += 1
            lo, hi = min(lo, level), max(hi, level)
        played += 1
    samples = played * 8
    return {"region": "NTSC" if region == 0 else "PAL", "address": "$%04X" % start, "length": length,
            "complete": complete and played == length, "period": period, "rate_hz": "%.2f" % (CPU[region] / period),
            "duration": "%.6f" % (samples * period / CPU[region]), "loop": bool(r0 & 0x40), "irq": bool(r0 & 0x80),
            "initial_level": r1, "final_level": level, "min_level": lo, "max_level": hi, "level_changes": changes}
