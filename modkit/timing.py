"""Play-through duration."""
from fractions import Fraction


def duration(m):
    speed, tempo = 6, 125
    rows = m["song_length"] * 64
    return Fraction(rows * speed * 5, tempo * 2)
