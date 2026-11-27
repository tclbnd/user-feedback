"""SNK wave generator snapshots (.snkw): one or more single-voice generators.

Each generator is a numerically controlled oscillator (NCO):

* F1/F2 latch a 12-bit phase increment. F1 supplies the high six bits
  (positions 6..11) and F2 the low six bits (positions 0..5); the top two
  data lines of each latch are unconnected. Every generator clock the
  increment is added to a 12-bit accumulator and each overflow advances the
  16-step waveform position by one. A larger increment therefore means a
  *higher* pitch; increment zero freezes the position (no overflow ever
  happens).
* W3..W6 hold the eight programmable waveform steps. Each byte carries two
  4-bit levels, high nibble first (the earlier step), followed by the low
  nibble. The levels are offset-binary: 8 is zero, so the signed output level
  is ``(nibble - 8) * 16``. The remaining eight steps of the 16-step cycle are
  not programmable and play zero (silence).
* One output sample is taken every 256 generator clocks, the waveform position
  and accumulator both starting at zero after reset.
"""

CLOCK = 6000000
CLOCKS_PER_SAMPLE = 256
NCO_MOD = 1 << 12          # 12-bit phase accumulator
WAVE_STEPS = 16
SAMPLE_COUNT = 512
NIBBLE_MID = 8             # offset-binary zero level
LEVEL_SCALE = 16           # 4-bit level -> signed output unit
PCM_SCALE = 256            # signed output unit -> 16-bit PCM sample


class SnkError(ValueError):
    pass


def synth(freq, wave, n=SAMPLE_COUNT):
    """Render the generator output, one sample every 256 clocks, from reset.

    The 12-bit accumulator starts at zero and the waveform position at step 0.
    Each accumulator overflow (modulo 4096) moves the position one step on.
    With a zero increment the position never moves, so a constant level is
    emitted rather than silence or an undefined pitch.
    """
    acc = 0
    pos = 0
    out = []
    for _ in range(n):
        out.append(wave[pos] * PCM_SCALE)
        if (acc & (NCO_MOD - 1)) + freq >= NCO_MOD:
            pos = (pos + 1) % WAVE_STEPS
        acc = (acc + freq) & 0xFFFF
    return out


def pcm(samples):
    import zlib
    return {"rate_hz": "%.1f" % (CLOCK / CLOCKS_PER_SAMPLE), "samples": len(samples),
            "peak": max(abs(v) for v in samples), "sum": sum(samples),
            "crc32": "%08x" % zlib.crc32(b"".join(v.to_bytes(2, "little", signed=True) for v in samples))}


def parse(data):
    if len(data) < 9 or data[:8] != b"SNKWAVE\0":
        raise SnkError("not an SNK wave snapshot")
    n = data[8]
    if len(data) < 9 + 6 * n:
        raise SnkError("register block is truncated")
    out = []
    for i in range(n):
        f1, f2, w3, w4, w5, w6 = data[9 + 6 * i:15 + 6 * i]
        # F1 latches the high six bits (positions 6..11), F2 the low six.
        freq = ((f1 & 0x3F) << 6) | (f2 & 0x3F)
        # High nibble of each waveform byte is the earlier of its two steps.
        nibbles = []
        for byte in (w3, w4, w5, w6):
            nibbles.append(byte >> 4)
            nibbles.append(byte & 0x0F)
        wave = [(nibble - NIBBLE_MID) * LEVEL_SCALE for nibble in nibbles]
        # Only the first eight steps are programmable; the rest play silence.
        wave.extend([0] * (WAVE_STEPS - len(wave)))
        samples = synth(freq, wave)
        # A voice is silent only when nothing audible comes out: the position
        # is frozen on a zero level (e.g. a zero increment starting at step 0).
        silent = all(sample == 0 for sample in samples)
        # A zero increment freezes the accumulator, so no pitch exists even if
        # the frozen level itself is audible. One full 16-step cycle otherwise
        # needs 16 * 4096 generator clocks per increment.
        cycle_hz = None if freq == 0 else (
            CLOCK * freq) / (NCO_MOD * WAVE_STEPS * CLOCKS_PER_SAMPLE)
        out.append({"voice": i + 1, "frequency": freq, "silent": silent,
                    "cycle_hz": None if cycle_hz is None else "%.3f" % cycle_hz,
                    "wave": wave, "wave_min": min(wave), "wave_max": max(wave),
                    "wave_sum": sum(wave), "pcm": pcm(samples)})
    return {"voices": out}
