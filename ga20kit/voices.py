"""Irem GA20 snapshots (.ga20): four PCM voices, start/end registers, rate, volume and the sample ROM.

Hardware rules (MAME iremga20):

* Start/end registers hold a 16-bit word in low/high byte order. The chip
  addresses sample ROM in 16-byte paragraphs, so the word is shifted left by
  four bits to form a byte address (word * 16).
* The control register is a key-on trigger: any non-zero value starts the
  voice, zero leaves it stopped (it is not an enable flag tested per bit).
* Rate: the chip ticks at CLOCK / 4 and advances (256 - rate) ROM bytes per
  tick, so the effective sample rate is CLOCK / (4 * (256 - rate)).
* Volume: the register is scaled to the chip's 64-step gain,
  effective volume = register >> 2.
* Samples are 8-bit unsigned PCM centred at 0x80; the signed value of a byte
  is byte - 128.
"""

CLOCK = 3579545
HDR = 8 + 32
ADDR_SHIFT = 4
VOLUME_SHIFT = 2
SILENCE_BIAS = 128


class Ga20Error(ValueError):
    pass


def parse(data):
    if len(data) < 8 or data[:8] != b"GA20SNAP":
        raise Ga20Error("not a GA20 snapshot")
    if len(data) < HDR:
        raise Ga20Error("register block is truncated")
    regs = data[8:HDR]
    rom = data[HDR:]
    rom_size = len(rom)
    voices = []
    for ch in range(4):
        r = regs[8 * ch:8 * ch + 8]
        start = (r[0] | r[1] << 8) << ADDR_SHIFT
        end = (r[2] | r[3] << 8) << ADDR_SHIFT
        rate = r[4]
        vol = r[5]
        on = r[6] != 0
        step = 256 - rate
        v = {"voice": ch + 1, "on": on, "start": start, "end": end,
             "volume": vol >> VOLUME_SHIFT,
             "rate_hz": "%.2f" % (CLOCK / (4 * step)),
             "length": None, "peak": None, "sum": None, "terminated": False}
        if on:
            vals = [rom[a] - SILENCE_BIAS
                    for a in range(start, min(end, rom_size))]
            v["terminated"] = end <= rom_size
            v["length"] = len(vals)
            v["peak"] = max((abs(x) for x in vals), default=0)
            v["sum"] = sum(vals)
        voices.append(v)
    return {"rom_size": rom_size,
            "voices_on": sum(v["on"] for v in voices),
            "voices": voices}

