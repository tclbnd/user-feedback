"""Minimal PGM/PPM writers and coverage visualizers (stdlib only)."""

from .coverage import popcount


def _write_pnm(path, header, rows):
    with open(path, "wb") as fh:
        fh.write(header.encode("ascii"))
        for row in rows:
            fh.write(bytes(row))


def write_coverage_pgm(path, mask, n=4):
    """Gray level per pixel = covered sample fraction (0..255)."""
    height = len(mask)
    width = len(mask[0]) if height else 0
    header = "P5\n%d %d\n255\n" % (width, height)
    rows = [[round(255 * popcount(w) / (n * n)) for w in row] for row in mask]
    _write_pnm(path, header, rows)


def write_rgba_ppm(path, rgba):
    """Premultiplied RGBA flattened over opaque white, written as binary PPM."""
    height = len(rgba)
    width = len(rgba[0]) if height else 0
    header = "P6\n%d %d\n255\n" % (width, height)
    rows = []
    for row in rgba:
        line = []
        for px in row:
            r, g, b, a = px
            line.append(min(255, round(255 * (r + (1.0 - a)))))
            line.append(min(255, round(255 * (g + (1.0 - a)))))
            line.append(min(255, round(255 * (b + (1.0 - a)))))
        rows.append(line)
    _write_pnm(path, header, rows)


def write_mask_diff_pgm(path, mask_a, mask_b, n=4):
    """Difference of two 16-bit masks; full white marks a full-sample flip."""
    height = len(mask_a)
    width = len(mask_a[0]) if height else 0
    header = "P5\n%d %d\n255\n" % (width, height)
    rows = []
    for ya, yb in zip(mask_a, mask_b):
        line = []
        for wa, wb in zip(ya, yb):
            line.append(round(255 * popcount(wa ^ wb) / (n * n)))
        rows.append(line)
    _write_pnm(path, header, rows)


def write_rgba_diff_ppm(path, rgba_a, rgba_b):
    """Per-channel absolute RGBA difference, amplified for visibility."""
    height = len(rgba_a)
    width = len(rgba_a[0]) if height else 0
    header = "P6\n%d %d\n255\n" % (width, height)
    rows = []
    for ra, rb in zip(rgba_a, rgba_b):
        line = []
        for pa, pb in zip(ra, rb):
            for ca, cb in zip(pa, pb):
                line.append(min(255, round(abs(ca - cb) * 255 * 16)))
        rows.append(line)
    _write_pnm(path, header, rows)
