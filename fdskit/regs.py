"""Famicom Disk System audio snapshots (.fdsa): pitch, modulation, volume and the 64-step wavetable."""
from fractions import Fraction

CPU = 1789773
HDR = 8 + 11 + 2
MASTER = (Fraction(1), Fraction(3, 4), Fraction(1, 2), Fraction(1, 4))
MOD_STEP = (0, 1, 2, 3, "reset", -3, -2, -1)


class FdsError(ValueError):
    pass


def parse(data):
    if len(data) < 8 or data[:8] != b"FDSAUDIO":
        raise FdsError("not an FDS audio snapshot")
    if len(data) < HDR:
        raise FdsError("register block is truncated")
    r = data[8:19]
    vgain, mgain = data[19] & 0x3F, data[20] & 0x3F
    wave = data[HDR:HDR + 64]
    mod = data[HDR + 64:HDR + 96]
    pitch = r[2] << 4 | (r[3] & 15)
    halted = bool(r[3] & 0x80)
    counter = r[5] & 0x7F
    modp = r[6] | (r[7] & 15) << 8
    level = Fraction(vgain, 32) * MASTER[r[9] & 3]
    out = {"pitch": pitch, "halted": halted, "envelopes_off": bool(r[3] & 0x40),
           "wave_write": bool(r[9] & 0x80), "mod_counter": counter,
           "freq_hz": None if halted else "%.3f" % (CPU * pitch / 16 / 2 ** 12),
           "mod_freq_hz": "%.3f" % (CPU * modp / 16 / 2 ** 12),
           "level": str(level), "complete": len(wave) == 64 and len(mod) == 32}
    if not out["complete"]:
        return {**out, "wave_min": None, "wave_max": None, "wave_sum": None, "mod_table": None}
    w = list(wave)
    return {**out, "wave_min": min(w), "wave_max": max(w), "wave_sum": sum(w),
            "mod_table": [MOD_STEP[b & 7] for b in mod]}
