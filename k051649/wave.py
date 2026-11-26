"""Konami SCC (K051649) and SCC+ register snapshots (.k51): waveform RAM, periods, volumes, channel enable."""

CLOCK = 3579545
WAVE_SIZE = 32
REGISTER_BYTES = 16

# SCC has four 32-byte waveform slots; channels 4 and 5 share slot 4.
# SCC+ has five independent 32-byte slots, followed by the register block.
REGISTER_BASE = {False: 0x80, True: 0xA0}
WAVE_SLOTS = {
    False: (0, 1, 2, 3, 3),
    True: (0, 1, 2, 3, 4),
}


class SnapshotError(ValueError):
    pass


def signed8(value):
    """Interpret a waveform RAM byte as two's-complement signed sample."""
    return value - 0x100 if value & 0x80 else value


def parse(data):
    if len(data) < 9 or data[:8] != b"K051649\0":
        raise SnapshotError("not an SCC snapshot")
    plus = bool(data[8] & 1)
    img = data[9:]
    base = REGISTER_BASE[plus]
    need = base + REGISTER_BYTES
    if len(img) < need:
        return {"mode": "SCC+" if plus else "SCC", "complete": False, "channels": None}

    slots = WAVE_SLOTS[plus]
    chans = []
    for ch in range(5):
        slot = slots[ch]
        start = WAVE_SIZE * slot
        vals = [signed8(b) for b in img[start:start + WAVE_SIZE]]

        # Frequency period is a little-endian 12-bit register value.
        period_low = img[base + 2 * ch]
        period_high = img[base + 2 * ch + 1] & 0x0F
        period = period_low | (period_high << 8)
        volume = img[base + 10 + ch] & 0x0F
        on = bool((img[base + 15] >> ch) & 1)
        freq_hz = "%.3f" % (CLOCK / (32 * period)) if period else None

        chans.append({
            "on": on,
            "period": period,
            "freq_hz": freq_hz,
            "volume": volume,
            "wave_slot": slot + 1,
            "wave_min": min(vals),
            "wave_max": max(vals),
            "wave_sum": sum(vals),
            "audible": on and volume > 0 and any(vals),
        })
    return {"mode": "SCC+" if plus else "SCC", "complete": True, "channels": chans}
