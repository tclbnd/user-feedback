"""Bitrate/quality points for the lab's rate curves."""
import math


def points(results):
    """results: list of (kbps, psnr) -> sorted unique points in log-rate."""
    seen = {}
    for kbps, q in results:
        seen[kbps] = max(q, seen.get(kbps, float("-inf")))
    return [(math.log10(k), q) for k, q in sorted(seen.items())]


def average_gain(a, b):
    """Mean quality difference of b over a at the rates both measured."""
    qa, qb = dict(a), dict(b)
    shared = sorted(set(qa) & set(qb))
    return round(sum(qb[r] - qa[r] for r in shared) / len(shared), 4) if shared else None
