"""Minimal deterministic vector math (plain tuples, no third party)."""
from math import sqrt


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def norm3(v):
    l = sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if l <= 1e-12:
        return (0.0, 0.0, 1.0)
    return (v[0] / l, v[1] / l, v[2] / l)


def bary2d(p, a, b, c):
    """Barycentric weights (w0,w1,w2) for p vs triangle (a,b,c) in 2D.

    Works for either winding; mirrored UV triangles simply return a
    negative signed area which callers may record as a mirror flag.
    Returns None for degenerate UV triangles.
    """
    den = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
    if abs(den) <= 1e-15:
        return None
    w0 = ((b[1] - c[1]) * (p[0] - c[0]) +
          (c[0] - b[0]) * (p[1] - c[1])) / den
    w1 = ((c[1] - a[1]) * (p[0] - c[0]) +
          (a[0] - c[0]) * (p[1] - c[1])) / den
    w2 = 1.0 - w0 - w1
    return (w0, w1, w2)
