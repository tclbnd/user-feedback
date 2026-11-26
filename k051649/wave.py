"""Konami SCC (K051649) and SCC+ register snapshots (.k51): waveform RAM, periods, volumes, channel enable."""
CLOCK = 3579545


class SnapshotError(ValueError):
    pass


def parse(data):
    if len(data) < 9 or data[:8] != b"K051649\0":
        raise SnapshotError("not an SCC snapshot")
    plus = bool(data[8] & 1)
    img = data[9:]
    base = 0x80
    need = base + 16
    if len(img) < need:
        return {"mode": "SCC+" if plus else "SCC", "complete": False, "channels": None}
    chans = []
    for ch in range(5):
        w = ch
        vals = list(img[32 * w:32 * w + 32])
        p = img[base + 2 * ch] << 8 | img[base + 2 * ch + 1]
        vol = img[base + 10 + ch] >> 4
        on = bool(img[base + 15] >> ch & 1)
        chans.append({"on": on, "period": p, "freq_hz": "%.3f" % (CLOCK / (32 * p)) if p else None, "volume": vol,
                      "wave_slot": w + 1, "wave_min": min(vals), "wave_max": max(vals), "wave_sum": sum(vals),
                      "audible": on and vol > 0 and any(vals)})
    return {"mode": "SCC+" if plus else "SCC", "complete": True, "channels": chans}
