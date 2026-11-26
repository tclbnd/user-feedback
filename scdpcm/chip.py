"""Sega CD RF5C164 PCM snapshots (.scp): channel on/off register, eight channels, 64 KB wave RAM."""

RATE = 32000


class SnapshotError(ValueError):
    pass


def _value(b):
    return b - 256 if b & 0x80 else b


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
        c = {"on": bool(off >> i & 1), "start": st << 8, "loop": st << 8, "rate_hz": "%.2f" % (fd / 256 * RATE),
             "left_gain": (pan >> 4) * env, "right_gain": (pan & 15) * env,
             "length": None, "loop_length": None, "peak": None, "sum": None, "duration": None}
        if complete:
            first = _run(ram, st << 8)
            if first is not None:
                c["length"] = len(first)
                c["peak"] = max((abs(v) for v in first), default=0)
                c["sum"] = sum(first)
                c["duration"] = "%.4f" % (len(first) / (fd / 256 * RATE)) if fd else None
            lp = _run(ram, st << 8)
            c["loop_length"] = None if lp is None else len(lp)
        chans.append(c)
    return {"complete": complete, "channels_on": sum(1 for c in chans if c["on"]), "channels": chans}
