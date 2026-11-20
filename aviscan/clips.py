"""Evidence clips: which key frames bound a requested time range."""
from fractions import Fraction


def key_times(stream, keys):
    """keys: list of frame numbers that are key frames."""
    rate = Fraction(stream["rate"])
    return [str(k / rate) for k in keys]


def bounds(key_frames, first, last):
    """Nearest key frame at or before first, and the last frame to copy."""
    start = max((k for k in key_frames if k <= first), default=0)
    return start, last
