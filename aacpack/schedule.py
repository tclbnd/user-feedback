"""Archive cut list: which capture files make up each programme hour."""
from fractions import Fraction


def hour_slots(files, start_of_day=0):
    """files: list of (name, start_seconds, duration) -> {hour: [names]}"""
    out = {}
    for name, start, duration in files:
        begin = Fraction(start) - start_of_day
        end = begin + Fraction(duration)
        hour = int(begin // 3600)
        while hour * 3600 < end:
            out.setdefault(hour, []).append(name)
            hour += 1
    return out


def gaps(files):
    """Holes between consecutive captures, in seconds."""
    ordered = sorted(files, key=lambda f: Fraction(f[1]))
    out = []
    for (a, sa, da), (b, sb, _) in zip(ordered, ordered[1:]):
        hole = Fraction(sb) - (Fraction(sa) + Fraction(da))
        if hole > 0:
            out.append({"after": a, "before": b, "seconds": str(hole)})
    return out
