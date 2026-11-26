"""Famicom Disk System audio snapshots (.fdsa): pitch, modulation, volume and the 64-step wavetable."""
from fractions import Fraction

CPU = 1789773
HDR = 8 + 11 + 2
WAVE_STEPS = 64
MOD_STEPS = 32
PHASE_SCALE = 2 ** 16
MASTER = (Fraction(1), Fraction(2, 3), Fraction(1, 2), Fraction(2, 5))
# The low three bits are a sign bit followed by a two-bit magnitude.  Code 4
# is negative zero in that encoding and the FDS uses it to reset the counter.
MOD_STEP = (0, 1, 2, 3, "reset", -1, -2, -3)


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
    pitch = r[2] | ((r[3] & 0x0F) << 8)
    halted = bool(r[3] & 0x80)
    counter = r[5] & 0x7F
    if counter & 0x40:
        counter -= 0x80
    modp = r[6] | ((r[7] & 0x0F) << 8)
    level = Fraction(vgain, 32) * MASTER[r[9] & 3]

    # The signed modulation counter and its envelope gain form the offset
    # added to the 12-bit F-number.
    tuned_pitch = pitch + counter * mgain
    cycle_divisor = WAVE_STEPS * PHASE_SCALE
    mod_cycle_divisor = MOD_STEPS * PHASE_SCALE
    freq_hz = None if halted else "%.3f" % (CPU * tuned_pitch / cycle_divisor)

    out = {"pitch": pitch, "halted": halted, "envelopes_off": bool(r[3] & 0x40),
           "wave_write": bool(r[9] & 0x80), "mod_counter": counter,
           "freq_hz": freq_hz,
           "mod_freq_hz": "%.3f" % (CPU * modp / mod_cycle_divisor),
           "level": str(level), "complete": len(wave) == 64 and len(mod) == 32}
    if not out["complete"]:
        return {**out, "wave_min": None, "wave_max": None, "wave_sum": None, "mod_table": None}
    w = [b & 0x3F for b in wave]
    return {**out, "wave_min": min(w), "wave_max": max(w), "wave_sum": sum(w),
            "mod_table": [MOD_STEP[b & 7] for b in mod]}
