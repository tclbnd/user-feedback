"""Per-pixel conversion tracing and range tagging.

Every conversion can be recorded as a flat list of small dicts, which is
what the delivery-side debugging needs: for one pixel you can see the
input codes, the normalised values, the exact matrix output, the
unrounded target code, whether it had to be clipped, and which range /
standard / chroma-phase convention produced it.

Tracing is opt-in (``Trace(enabled=False)`` costs one attribute check per
pixel) because a 10^5 pixel regression run does not want the payload.
"""

from fractions import Fraction

__all__ = ["Trace", "RangeTags", "frac_to_json"]


def frac_to_json(value):
    """Render a Fraction as a float with a short stable repr."""
    if isinstance(value, Fraction):
        return round(float(value), 9)
    return value


class RangeTags:
    """Immutable-ish marker describing the conventions used for a pixel."""

    __slots__ = ("standard", "rgb_range", "yuv_range", "layout",
                 "chroma_phase", "path")

    def __init__(self, standard, rgb_range, yuv_range, layout="4:4:4",
                 chroma_phase=(0.0, 0.0), path="exact"):
        self.standard = standard
        self.rgb_range = rgb_range
        self.yuv_range = yuv_range
        self.layout = layout
        self.chroma_phase = tuple(chroma_phase)
        self.path = path

    def describe(self):
        return {
            "standard": self.standard,
            "rgb_range": self.rgb_range,
            "yuv_range": self.yuv_range,
            "layout": self.layout,
            "chroma_phase": [float(self.chroma_phase[0]),
                             float(self.chroma_phase[1])],
            "path": self.path,
        }

    def key(self):
        return (self.standard, self.rgb_range, self.yuv_range,
                self.layout, self.chroma_phase, self.path)


class Trace:
    """Collects per-pixel conversion records."""

    __slots__ = ("enabled", "records", "limit", "dropped")

    def __init__(self, enabled=False, limit=None):
        self.enabled = bool(enabled)
        self.records = []
        self.limit = limit
        self.dropped = 0

    def add(self, record):
        if not self.enabled:
            return
        if self.limit is not None and len(self.records) >= self.limit:
            self.dropped += 1
            return
        self.records.append(record)

    def summary(self):
        if not self.enabled:
            return None
        return {
            "pixels": len(self.records),
            "dropped": self.dropped,
            "limit": self.limit,
        }

