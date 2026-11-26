"""YM2413 (OPLL) register snapshots (.opll): melody channels, built-in instruments, rhythm section."""
CLOCK = 3579545
INSTRUMENTS = ["custom", "violin", "guitar", "piano", "flute", "clarinet", "oboe", "trumpet", "organ", "horn",
               "synthesizer", "harpsichord", "vibraphone", "synth bass", "acoustic bass", "electric guitar"]


class SnapshotError(ValueError):
    pass


def _volume_db(level):
    """Convert a four-bit YM2413 attenuation value to decibels."""
    return 0.0 if level == 0 else -3.0 * level


def _melody_channel(r, k):
    frequency = r[0x10 + k] | ((r[0x20 + k] & 0x01) << 8)
    block = (r[0x20 + k] >> 1) & 0x07
    control = r[0x20 + k]
    channel = r[0x30 + k]

    return {
        "channel": k + 1,
        "key_on": bool(control & 0x10),
        "sustain": bool(control & 0x20),
        "instrument": INSTRUMENTS[channel & 0x0F],
        "volume_db": _volume_db(channel >> 4),
        "fnum": frequency,
        "block": block,
        "freq_hz": "%.3f" % (frequency * CLOCK / 72 / (1 << (19 - block))),
    }


def _drum(control, key_mask, level):
    return {"on": bool(control & key_mask), "volume_db": _volume_db(level)}


def parse(data):
    if len(data) < 8 or data[:8] != b"YM2413\0\0":
        raise SnapshotError("not a YM2413 snapshot")
    r = data[8:8 + 64]
    if len(r) < 64:
        return {"complete": False, "rhythm_mode": None, "melodic": None, "drums": None}

    rhythm_control = r[0x0E]
    rhythm = bool(rhythm_control & 0x20)
    channel_count = 6 if rhythm else 9
    mel = [_melody_channel(r, k) for k in range(channel_count)]

    drums = None
    if rhythm:
        drums = {
            "bass drum": _drum(rhythm_control, 0x10, r[0x36] >> 4),
            "snare drum": _drum(rhythm_control, 0x08, r[0x37] & 0x0F),
            "tom-tom": _drum(rhythm_control, 0x04, r[0x38] >> 4),
            "top cymbal": _drum(rhythm_control, 0x02, r[0x38] & 0x0F),
            "hi-hat": _drum(rhythm_control, 0x01, r[0x37] >> 4),
        }

    return {"complete": True, "rhythm_mode": rhythm, "melodic": mel, "drums": drums}
