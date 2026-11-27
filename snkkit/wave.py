"""SNK wave generator snapshots (.snkw): one or more single-voice generators with a mirrored 16-step waveform."""

CLOCK = 6000000


class SnkError(ValueError):
    pass


def synth(freq, wave, n=512):
    """Output samples at the generator's output rate, stepping through the waveform at cycle_hz."""
    out = []
    if not freq:
        return [0] * n
    rate = CLOCK / 256
    step = CLOCK / (freq * 16) * 16 / rate
    phase = 0.0
    for _ in range(n):
        out.append(wave[int(phase) % 16] * 256)
        phase += step
    return out


def pcm(samples):
    import zlib
    return {"rate_hz": "%.1f" % (CLOCK / 256), "samples": len(samples), "peak": max(abs(v) for v in samples),
            "sum": sum(samples), "crc32": "%08x" % zlib.crc32(b"".join(v.to_bytes(2, "little", signed=True) for v in samples))}


def parse(data):
    if len(data) < 9 or data[:8] != b"SNKWAVE\0":
        raise SnkError("not an SNK wave snapshot")
    n = data[8]
    if len(data) < 9 + 6 * n:
        raise SnkError("register block is truncated")
    out = []
    for i in range(n):
        r = data[9 + 6 * i:15 + 6 * i]
        freq = (r[0] & 0x3F) << 6 | (r[1] & 0x3F)
        wave = [0] * 16
        for k in range(4):
            wave[2 * k] = (r[2 + k] & 0x0F) << 4
            wave[2 * k + 1] = (r[2 + k] >> 4) << 4
        for k in range(8):
            wave[8 + k] = wave[k]
        silent = freq == 0
        out.append({"voice": i + 1, "frequency": freq, "silent": silent,
                    "cycle_hz": None if silent else "%.3f" % (CLOCK / (freq * 16)),
                    "wave": wave, "wave_min": min(wave), "wave_max": max(wave), "wave_sum": sum(wave),
                    "pcm": pcm(synth(freq, wave))})
    return {"voices": out}
