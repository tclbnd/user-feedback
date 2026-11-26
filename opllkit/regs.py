"""YM2413 (OPLL) register snapshots (.opll): melody channels, built-in instruments, rhythm section."""
CLOCK = 3579545
INSTRUMENTS = ["custom", "violin", "guitar", "piano", "flute", "clarinet", "oboe", "trumpet", "organ", "horn",
               "synthesizer", "harpsichord", "vibraphone", "synth bass", "acoustic bass", "electric guitar"]


class SnapshotError(ValueError):
    pass


def parse(data):
    if len(data) < 8 or data[:8] != b"YM2413\0\0":
        raise SnapshotError("not a YM2413 snapshot")
    r = data[8:8 + 64]
    if len(r) < 64:
        return {"complete": False, "rhythm_mode": None, "melodic": None, "drums": None}
    rhythm = False
    mel = []
    for k in range(9):
        fnum = r[0x10 + k]
        block = r[0x20 + k] >> 1 & 7
        mel.append({"channel": k + 1, "key_on": bool(r[0x20 + k] & 0x20), "sustain": bool(r[0x20 + k] & 0x10),
                    "instrument": INSTRUMENTS[r[0x30 + k] & 15], "volume_db": 3.0 * (r[0x30 + k] >> 4),
                    "fnum": fnum, "block": block, "freq_hz": "%.3f" % (fnum * CLOCK / 72 / (1 << (19 - block)))})
    return {"complete": True, "rhythm_mode": rhythm, "melodic": mel, "drums": None}
