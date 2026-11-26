"""NES DMC channel snapshots (.ndmc): sample address, length, rate and the delta-modulated output level."""

# CPU cycles per output bit, indexed by the 4-bit rate field in $4010.
NTSC_RATE = [428, 380, 340, 320, 298, 254, 226, 214, 190, 160, 142, 128, 106, 84, 72, 54]
PAL_RATE = [398, 354, 316, 298, 276, 236, 210, 198, 176, 148, 132, 118, 98, 78, 66, 50]
RATES = {0: NTSC_RATE, 1: PAL_RATE}
CPU = {0: 1789773, 1: 1662607}

# $4011 holds a 7-bit output level; the DMC unit can only move it within 0..127.
LEVEL_MIN, LEVEL_MAX = 0, 127
STEP = 2


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

    period = RATES[region][r0 & 15]
    # $4012: sample address = $C000 + index * 64
    start = 0xC000 + r2 * 64
    # $4013: sample length in bytes = index * 64 + 1
    length = r3 * 64 + 1

    level = r1 & 0x7F
    lo = hi = level
    changes = 0
    played = 0
    for i in range(length):
        # The sample address counter wraps inside the $8000-$FFFF PRG window.
        a = 0x8000 + ((start - 0x8000 + i) & 0x7FFF)
        idx = a - 0x8000
        if idx >= len(prg):
            break
        b = prg[idx]
        # Bits are shifted out least significant bit first. A set bit raises
        # the level by 2, a clear bit lowers it by 2, clamped to 0..127; a bit
        # that cannot move the level is a silence cycle and changes nothing.
        for k in range(8):
            if (b >> k) & 1:
                if level <= LEVEL_MAX - STEP:
                    level += STEP
                    changes += 1
            else:
                if level >= LEVEL_MIN + STEP:
                    level -= STEP
                    changes += 1
            lo, hi = min(lo, level), max(hi, level)
        played += 1
    samples = played * 8
    return {"region": "NTSC" if region == 0 else "PAL", "address": "$%04X" % start, "length": length,
            "complete": complete and played == length, "period": period, "rate_hz": "%.2f" % (CPU[region] / period),
            "duration": "%.6f" % (samples * period / CPU[region]), "loop": bool(r0 & 0x40), "irq": bool(r0 & 0x80),
            "initial_level": r1 & 0x7F, "final_level": level, "min_level": lo, "max_level": hi,
            "level_changes": changes}
