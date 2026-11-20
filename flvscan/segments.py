"""Split a recording into review segments at key frames."""


def segment_starts(key_times_ms, length_ms=60000):
    out, next_cut = [], 0
    for t in key_times_ms:
        if t >= next_cut:
            out.append(t)
            next_cut = t + length_ms
    return out


def hms(ms):
    s = ms // 1000
    return f"{s // 3600:d}:{s // 60 % 60:02d}:{s % 60:02d}.{ms % 1000:03d}"
