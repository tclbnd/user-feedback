"""RAD Smacker video (.smk): header, frame tables, palettes, audio tracks."""
import struct
from fractions import Fraction


class SmkError(ValueError):
    pass


def _codec(flags):
    return "smacker" if flags & (1 << 31) else "pcm"


def frame_rate(raw):
    return Fraction(raw)


def parse(data):
    if len(data) < 104 or data[:3] != b"SMK" or data[3:4] not in (b"2", b"4"):
        raise SmkError("not a Smacker file")
    width, height, frames, rate_raw, flags = struct.unpack_from("<IIIiI", data, 4)
    audio_rates = struct.unpack_from("<7I", data, 72)
    trees, = struct.unpack_from("<I", data, 52)
    ring = bool(flags & 1)
    n = frames + (1 if ring else 0)
    p = 104
    if p + 5 * n > len(data):
        raise SmkError("frame tables run past the end of the file")
    sizes = struct.unpack_from("<%dI" % n, data, p)
    types = data[p + 4 * n:p + 5 * n]
    p += 5 * n + trees
    tracks = {}
    for t, v in enumerate(audio_rates):
        if v:
            tracks[t] = {"track": t, "sample_rate": v, "bits": 16 if v & (1 << 28) else 8,
                         "channels": 2 if v & (1 << 29) else 1, "codec": _codec(v), "bytes": 0}
    video = keyframes = palettes = 0
    for i in range(n):
        size = sizes[i]
        start, end = p, p + size
        if end > len(data):
            break
        q = start
        if types[i] & 1:
            q += data[q]
            palettes += 1
        for t in range(7):
            if types[i] & (2 << t):
                length, = struct.unpack_from("<I", data, q)
                if t in tracks:
                    tracks[t]["bytes"] += length
                q += length
        if q > end:
            raise SmkError("frame %d chunks overrun the frame size" % i)
        video += end - q
        if sizes[i] & 1:
            keyframes += 1
        p = end
    fps = frame_rate(rate_raw)
    return {"version": data[:4].decode(), "width": width, "height": height, "frames": n,
            "ring_frame": ring, "fps": str(fps), "duration": str(frames / fps),
            "interlaced": bool(flags & 2), "keyframes": keyframes, "palette_frames": palettes,
            "video_bytes": video, "audio": [tracks[t] for t in sorted(tracks)]}
