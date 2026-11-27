"""Konami 005289 snapshots (.k5289): two wavetable channels driven from a 4-bit wave PROM.

Hardware rules used here:

* Each channel's pitch is a 12-bit value on the CPU address bus, captured by the
  LD line into a latch. The value only reaches the running down-counter when the
  channel's TG line is asserted; until then the counter keeps the old frequency
  and the latched value stays "pending".
* The counter counts down and wraps at the 12-bit boundary, so the step period
  in chip clocks is ``0x1000 - pitch`` (a bigger latch value means a shorter
  period and therefore a higher note). A period of zero never advances.
* The AY control port byte packs the volume in its low four bits and the
  waveform number (0-7) in bits 4-6.
* The wave PROM holds 4-bit nibbles, two per byte, low nibble first. The 9-bit
  nibble address is channel (A8), waveform (A7-A5) and 32-step position
  (A4-A0); the two 256-byte channel banks are stored back to back in the file.
* Nibbles are unsigned 0-15 and centre at 8, so a signed sample is ``nibble-8``.
"""
import struct
import zlib

CLOCK = 3579545
HDR = 8 + 11
STEPS = 32
PERIOD_MOD = 0x1000


class BubError(ValueError):
    pass


def period_of(freq):
    """Step period in chip clocks for a 12-bit down-counter latch value."""
    return PERIOD_MOD - (freq & 0xFFF)


def read_nibble(prom, channel, waveform, pos):
    """One unsigned 4-bit PROM sample; bytes missing from the file read as 0."""
    nib_addr = (channel & 1) << 8 | (waveform & 7) << 5 | (pos & 0x1F)
    byte_index = nib_addr >> 1
    if byte_index >= len(prom):
        return 0
    byte = prom[byte_index]
    return byte & 0x0F if nib_addr & 1 == 0 else (byte >> 4) & 0x0F


def waveform_samples(prom, channel, waveform):
    """The 32 signed samples of one channel/waveform, or None if not in the file."""
    first_byte = ((channel & 1) << 8 | (waveform & 7) << 5) >> 1
    if len(prom) < first_byte + 16:
        return None
    return [read_nibble(prom, channel, waveform, pos) - 8 for pos in range(STEPS)]


def mix(chs, prom, n=65536):
    """Mixed output of the two channels at the chip clock, from reset."""
    out = []
    for t in range(n):
        v = 0
        for i, c in enumerate(chs):
            period = period_of(c["frequency"])
            pos = (t // period) & 0x1F if period else 0
            sample = read_nibble(prom, i, c["waveform"], pos) - 8
            v += sample * c["volume"]
        out.append(v)
    packed = b"".join(x.to_bytes(2, "little", signed=True) for x in out)
    return {"rate_hz": CLOCK, "samples": n, "peak": max(abs(x) for x in out), "sum": sum(out),
            "crc32": "%08x" % zlib.crc32(packed)}


def parse(data):
    if len(data) < 8 or data[:8] != b"K005289S":
        raise BubError("not a 005289 snapshot")
    if len(data) < HDR:
        raise BubError("register block is truncated")
    ld0, ld1, tg, ca, cb, o0, o1 = struct.unpack_from("<HHBBBHH", data, 8)
    prom = data[HDR:HDR + 512]
    out = []
    for ch, (ld, ctrl, old) in enumerate(((ld0, ca, o0), (ld1, cb, o1))):
        latched = ld & 0xFFF
        fired = bool(tg >> ch & 1)
        if fired:
            freq, pending = latched, None
        else:
            freq, pending = old & 0xFFF, latched
        volume = ctrl & 0x0F
        waveform = (ctrl >> 4) & 7
        prom_address = (ch & 1) << 8 | waveform << 5
        period = period_of(freq)
        c = {"channel": ch + 1, "volume": volume, "waveform": waveform,
             "prom_address": prom_address, "frequency": freq, "pending": pending,
             "cycle_hz": "%.3f" % (CLOCK / (STEPS * period)) if period else None,
             "wave_min": None, "wave_max": None, "wave_sum": None}
        vals = waveform_samples(prom, ch, waveform) if len(prom) == 512 else None
        if vals is not None:
            c.update(wave_min=min(vals), wave_max=max(vals), wave_sum=sum(vals))
        out.append(c)
    return {"prom_complete": len(prom) == 512, "channels": out, "output": mix(out, prom)}
