"""RAD Smacker video (.smk): header, frame tables, palettes, audio tracks."""
import struct
from fractions import Fraction

HEADER_SIZE = 104
N_TRACKS = 7


class SmkError(ValueError):
    pass


def _codec(word):
    """Codec of an audio rate word (top two bits)."""
    kind = (word >> 30) & 3
    if kind == 0:
        return "pcm"
    if kind == 1:
        return "bink-dct"
    if kind == 2:
        return "bink-rdft"
    return "smacker"


def frame_rate(raw):
    """Frame rate from the signed header word.

    Positive: milliseconds per frame; negative: the frame rate's numerator
    (the stored value is ``-1000/fps``).
    """
    if raw > 0:
        return Fraction(1000, raw)
    if raw < 0:
        return Fraction(1000, -raw)
    raise SmkError("invalid frame rate of zero")


def parse(data):
    if len(data) < HEADER_SIZE or data[:3] != b"SMK" or data[3:4] not in (b"2", b"4"):
        raise SmkError("not a Smacker file")
    width, height, frames, rate_raw, flags = struct.unpack_from("<IIIiI", data, 4)
    trees, = struct.unpack_from("<I", data, 52)
    audio_rates = struct.unpack_from("<7I", data, 72)
    ring = bool(flags & 1)
    interlaced = bool(flags & 2)
    # Interlaced and line-doubled videos store half the displayed height.
    if flags & 6:
        height *= 2
    n = frames + (1 if ring else 0)
    p = HEADER_SIZE
    if p + 5 * n > len(data):
        raise SmkError("frame tables run past the end of the file")
    sizes = struct.unpack_from("<%dI" % n, data, p)
    types = data[p + 4 * n:p + 5 * n]
    p += 5 * n + trees
    tracks = {}
    for t, word in enumerate(audio_rates):
        if word:
            tracks[t] = {"track": t, "sample_rate": word & 0xFFFFFF,
                         "bits": 16 if word & (1 << 29) else 8,
                         "channels": 2 if word & (1 << 28) else 1,
                         "codec": _codec(word), "bytes": 0}
    video = keyframes = palettes = 0
    for i in range(n):
        size = sizes[i] & 0xFFFFFFFE          # bit 0 flags a keyframe
        start, end = p, p + size
        if end > len(data):
            raise SmkError("frame %d runs past the end of the file" % i)
        q = start
        if types[i] & 1:                       # palette chunk
            count = data[q]                    # entries minus one
            q += (count + 1) * 4
            if i < frames:
                palettes += 1
        for t in range(N_TRACKS):
            if types[i] & (2 << t):
                length, = struct.unpack_from("<I", data, q)   # 0 = empty chunk
                if length and (length < 4 or q + length > end):
                    raise SmkError("frame %d track %d audio chunk overrun" % (i, t))
                if i < frames and t in tracks:
                    tracks[t]["bytes"] += max(length - 4, 0)
                q += length
        if q > end:
            raise SmkError("frame %d chunks overrun the frame size" % i)
        if i < frames:
            video += end - q
            if sizes[i] & 1:
                keyframes += 1
        p = end
    fps = frame_rate(rate_raw)
    return {"version": data[:4].decode(), "width": width, "height": height,
            "frames": frames, "ring_frame": ring, "fps": str(fps),
            "duration": str(frames / fps), "interlaced": interlaced,
            "keyframes": keyframes, "palette_frames": palettes,
            "video_bytes": video, "audio": [tracks[t] for t in sorted(tracks)]}
