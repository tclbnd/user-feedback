"""Sega CD RF5C164 PCM snapshots (.scp): channel on/off register, eight channels, 64 KB wave RAM."""

RATE = 32000
# RF5C164 frequency words are the increment of an address counter with 11
# fractional bits.
FREQ_DIVISOR = 1 << 11


class SnapshotError(ValueError):
    pass


def _value(b):
    """Decode RF5C164 sign-magnitude data; bit 7 selects positive level."""
    magnitude = b & 0x7F
    return magnitude if b & 0x80 else -magnitude


def _run(ram, a):
    """samples from a up to the end marker; None when there is none."""
    vals = []
    while a < len(ram):
        b = ram[a]
        if b == 0xFF:
            return vals
        vals.append(_value(b))
        a += 1
    return None


def parse(data):
    if len(data) < 9 + 56 or data[:8] != b"SCDPCM\0\0":
        raise SnapshotError("not an RF5C164 snapshot")
    off = data[8]
    ram = data[65:65 + 65536]
    complete = len(ram) == 65536
    chans = []
    for i in range(8):
        env, pan, fdl, fdh, lsl, lsh, st = data[9 + 7 * i:16 + 7 * i]
        fd = fdh << 8 | fdl
        loop_addr = lsh << 8 | lsl
        rate = fd * RATE / FREQ_DIVISOR
        c = {"on": ((off >> i) & 1) == 0, "start": st << 8, "loop": loop_addr,
             "rate_hz": "%.2f" % rate,
             "left_gain": (pan & 0x0F) * env, "right_gain": (pan >> 4) * env,
             "length": None, "loop_length": None, "peak": None, "sum": None, "duration": None}
        if complete:
            first = _run(ram, st << 8)
            if first is not None:
                c["length"] = len(first)
                c["peak"] = max((abs(v) for v in first), default=0)
                c["sum"] = sum(first)
                c["duration"] = "%.4f" % (len(first) / rate) if fd else None
            lp = _run(ram, loop_addr)
            c["loop_length"] = None if lp is None else len(lp)
        chans.append(c)
    return {"complete": complete, "channels_on": sum(1 for c in chans if c["on"]), "channels": chans}
