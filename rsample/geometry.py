"""Path flattening and the shared boundary convention.

World coordinates are translated by -origin once, up front. The translation is
applied as an exact subtraction on the input doubles before any geometry is
built, so scenes with large coordinates and an integer origin are evaluated in
the same coordinate frame as the matching small-coordinate scene.

A path is flattened to oriented segments ``(x0, y0, x1, y1)``. Quadratic and
cubic Bezier curves use de Casteljau subdivision; repeated control points and
zero-area contours are allowed and simply collapse. Subpaths close on Z, on a
new M, or implicitly at the end of the command stream.

Sample grid convention (used identically by the production scanliner and the
independent winding reference):

* pixel (x, y) covers the half-open square [x, x+1) x [y, y+1);
* the 4x4 samples sit at x + (j + 0.5)/4, y + (i + 0.5)/4;
* a horizontal ray fires to +x from each sample;
* a crossing counts only when the lower endpoint is included and the upper
  endpoint excluded, and the intersection x is strictly right of the sample,
  so upward edges contribute +1 and downward edges -1; the two roots of a
  tangent cancel.
"""

# World-space flatness tolerance, in pixels. A chord this close to the curve
# keeps 1/4-pixel sample points faithful while bounding segment counts.
FLATNESS = 0.0625
MAX_SUBDIV = 24  # ~16M segments per curve; repeated control points bail early.


def translate_commands(commands, ox, oy):
    """Subtract the viewport origin before any arithmetic (exact for ints)."""
    out = []
    for cmd in commands:
        if cmd[0] == "Z":
            out.append(["Z"])
            continue
        moved = [cmd[0]]
        for k, v in enumerate(cmd[1:], start=1):
            moved.append(float(v - (ox if k % 2 else oy)))
        out.append(moved)
    return out


def _quad_flat(p0, p1, p2):
    # Twice the area of the control triangle bounds the deviation of a
    # quadratic's control point from the chord; repeat handles cubic chains.
    dx = p2[0] - p0[0]
    dy = p2[1] - p0[1]
    if dx == 0.0 and dy == 0.0:
        d = abs(p1[0] - p0[0]) + abs(p1[1] - p0[1])
    else:
        d = abs(dx * (p0[1] - p1[1]) - dy * (p0[0] - p1[0])) / (dx * dx + dy * dy) ** 0.5
    return d <= FLATNESS


def _cubic_flat(p0, p1, p2, p3):
    # Standard bound: max distance of the two interior control points from the
    # end-point chord, scaled by 3/4, overestimates the true curve deviation.
    dx = p3[0] - p0[0]
    dy = p3[1] - p0[1]
    if dx == 0.0 and dy == 0.0:
        d = max(abs(p1[0] - p0[0]) + abs(p1[1] - p0[1]),
                abs(p2[0] - p0[0]) + abs(p2[1] - p0[1]))
    else:
        inv = 1.0 / (dx * dx + dy * dy) ** 0.5
        d1 = abs(dx * (p0[1] - p1[1]) - dy * (p0[0] - p1[0])) * inv
        d2 = abs(dx * (p0[1] - p2[1]) - dy * (p0[0] - p2[0])) * inv
        d = max(d1, d2) * 0.75
    return d <= FLATNESS


def _subdivide_quad(p0, p1, p2, depth, sink):
    if depth == 0 or _quad_flat(p0, p1, p2):
        sink((p0, p2))
        return
    m01 = ((p0[0] + p1[0]) * 0.5, (p0[1] + p1[1]) * 0.5)
    m12 = ((p1[0] + p2[0]) * 0.5, (p1[1] + p2[1]) * 0.5)
    m = ((m01[0] + m12[0]) * 0.5, (m01[1] + m12[1]) * 0.5)
    _subdivide_quad(p0, m01, m, depth - 1, sink)
    _subdivide_quad(m, m12, p2, depth - 1, sink)


def _subdivide_cubic(p0, p1, p2, p3, depth, sink):
    if depth == 0 or _cubic_flat(p0, p1, p2, p3):
        sink((p0, p3))
        return
    m01 = ((p0[0] + p1[0]) * 0.5, (p0[1] + p1[1]) * 0.5)
    m12 = ((p1[0] + p2[0]) * 0.5, (p1[1] + p2[1]) * 0.5)
    m23 = ((p2[0] + p3[0]) * 0.5, (p2[1] + p3[1]) * 0.5)
    a = ((m01[0] + m12[0]) * 0.5, (m01[1] + m12[1]) * 0.5)
    b = ((m12[0] + m23[0]) * 0.5, (m12[1] + m23[1]) * 0.5)
    m = ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)
    _subdivide_cubic(p0, m01, a, m, depth - 1, sink)
    _subdivide_cubic(m, b, m23, p3, depth - 1, sink)


def flatten_path(commands, ox=0.0, oy=0.0):
    """Flatten translated path commands into a list of oriented segments.

    Each segment is ``(x0, y0, x1, y1)`` in translated pixel space. Zero-length
    segments are dropped (they can never cross a ray) but their endpoints still
    participate in closure, so a contour with a repeated point stays intact.
    """
    if ox or oy:
        commands = translate_commands(commands, ox, oy)

    segments = []
    start = None      # first point of the current subpath
    current = None    # last point reached

    def close_sub():
        if start is not None and current is not None and current != start:
            segments.append((current[0], current[1], start[0], start[1]))

    for cmd in commands:
        tag = cmd[0]
        if tag == "M":
            close_sub()
            start = current = (float(cmd[1]), float(cmd[2]))
        elif tag == "L":
            nxt = (float(cmd[1]), float(cmd[2]))
            if current is None:
                start = current = nxt
            else:
                if nxt != current:
                    segments.append((current[0], current[1], nxt[0], nxt[1]))
                current = nxt
        elif tag == "Q":
            if current is None:
                start = current = (float(cmd[3]), float(cmd[4]))
                continue
            p1 = (float(cmd[1]), float(cmd[2]))
            p2 = (float(cmd[3]), float(cmd[4]))
            chords = []
            _subdivide_quad(current, p1, p2, MAX_SUBDIV, chords.append)
            for a, b in chords:
                if a != b:
                    segments.append((a[0], a[1], b[0], b[1]))
            current = p2
        elif tag == "C":
            if current is None:
                start = current = (float(cmd[5]), float(cmd[6]))
                continue
            p1 = (float(cmd[1]), float(cmd[2]))
            p2 = (float(cmd[3]), float(cmd[4]))
            p3 = (float(cmd[5]), float(cmd[6]))
            chords = []
            _subdivide_cubic(current, p1, p2, p3, MAX_SUBDIV, chords.append)
            for a, b in chords:
                if a != b:
                    segments.append((a[0], a[1], b[0], b[1]))
            current = p3
        elif tag == "Z":
            close_sub()
            current = start
    close_sub()  # implicit closure at end of stream / last subpath
    return segments


def crossing_winding(seg, sy):
    """Signed ray contribution of one segment at scanline ``sy``.

    Returns ``None`` when the segment does not cross the sample row under the
    lower-inclusive / upper-exclusive rule, otherwise +1 (upward edge) or -1
    (downward edge) plus the intersection x. The caller compares x to the
    sample x with a strict greater-than test.
    """
    x0, y0, x1, y1 = seg
    if y0 == y1:
        return None
    if y0 < y1:  # upward edge: lower endpoint included, upper excluded
        if not (y0 <= sy < y1):
            return None
        sign = 1
        lo = (x0, y0)
        hi = (x1, y1)
    else:        # downward edge
        if not (y1 <= sy < y0):
            return None
        sign = -1
        lo = (x1, y1)
        hi = (x0, y0)
    t = (sy - lo[1]) / (hi[1] - lo[1])
    return sign, lo[0] + (hi[0] - lo[0]) * t
