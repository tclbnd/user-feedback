"""Oric HIRES screen dumps (.hir): 200 lines of 40 bytes."""

WIDTH, HEIGHT, PER_LINE = 240, 200, 40


class HiresError(ValueError):
    pass


def decode_line(row):
    """Colour (0-7) of each of the 240 pixels of one 40-byte line."""
    out = []
    for b in row:
        out.extend(7 if b >> (5 - i) & 1 else 0 for i in range(6))
    return out


def parse(data):
    if len(data) == 0 or len(data) > PER_LINE * HEIGHT:
        raise HiresError("not a HIRES screen")
    lines = len(data) // PER_LINE
    if lines == 0:
        raise HiresError("not a HIRES screen")
    hist = [0] * 8
    first = last = None
    for y in range(lines):
        cols = decode_line(data[y * PER_LINE:(y + 1) * PER_LINE])
        for c in cols:
            hist[c] += 1
        s = "".join(str(c) for c in cols)
        if y == 0:
            first = s
        last = s
    return {"lines": lines, "complete": len(data) == PER_LINE * HEIGHT, "histogram": hist,
            "colors_used": sum(1 for h in hist if h), "attribute_bytes": 0, "inverse_cells": 0,
            "first_line": first, "last_line": last}
