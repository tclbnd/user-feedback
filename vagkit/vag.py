"""PlayStation SPU ADPCM sounds (.vag): header, 16-byte frames, loop flags."""
import struct
from fractions import Fraction

COEF = ((0, 0), (60, 0), (115, -52), (98, -55), (122, -60))
HEADER_SIZE = 48
FRAME_SIZE = 16
SAMPLES_PER_FRAME = 28

LOOP_START = 0x04
LOOP = 0x02
END = 0x01


class VagError(ValueError):
    pass


def parse(data):
    if len(data) < HEADER_SIZE or data[:4] != b"VAGp":
        raise VagError("not a VAG file")

    # VAGp header fields are big-endian; the field at offset 8 is reserved.
    version, _, data_size, sample_rate = struct.unpack_from(">IIII", data, 4)
    name = data[32:48].split(b"\0", 1)[0].decode("latin-1")

    frames = data_size // FRAME_SIZE
    if frames > (len(data) - HEADER_SIZE) // FRAME_SIZE:
        raise VagError("VAG data is truncated")

    h1 = h2 = 0
    peak = 0
    last_sample = None
    samples = 0
    ended = False
    looped = False
    loop_start = None

    for frame_index in range(frames):
        start = HEADER_SIZE + frame_index * FRAME_SIZE
        fr = data[start:start + FRAME_SIZE]
        shift = fr[0] & 0x0F
        filt = fr[0] >> 4
        if filt >= len(COEF):
            raise VagError("bad filter %d" % filt)

        flags = fr[1]
        if flags & LOOP_START:
            loop_start = frame_index * SAMPLES_PER_FRAME

        f0, f1 = COEF[filt]
        for i in range(SAMPLES_PER_FRAME):
            # In each data byte the lower nibble is the earlier sample.
            nib = (fr[2 + i // 2] >> (4 * (i % 2))) & 0x0F
            d = ((nib - 16 if nib & 8 else nib) << 12) >> shift
            y = max(-32768, min(32767, d + ((h1 * f0 + h2 * f1 + 32) >> 6)))
            h2, h1 = h1, y
            peak = max(peak, abs(y))
            last_sample = y

        samples += SAMPLES_PER_FRAME

        if flags & END:
            ended = True
            looped = bool(flags & LOOP)
            break

    duration = str(Fraction(samples, sample_rate)) if sample_rate else None
    return {
        "name": name,
        "version": version,
        "sample_rate": sample_rate,
        "frames": frames,
        "samples": samples,
        "duration": duration,
        "ended": ended,
        "looped": looped,
        "loop_start": loop_start,
        "peak": "%.6f" % (peak / 32768),
        "last_sample": last_sample,
    }
