"""Atari 2600 scan line logs (.tia): rebuild what the TIA draws on each line.

The 22-byte per-line record holds, in order, COLUBK, COLUPF, COLUP0,
COLUP1, CTRLPF, PF0, PF1, PF2, GRP0, GRP1, NUSIZ0, NUSIZ1, REFP0,
REFP1, ENAM0, ENAM1, ENABL and the horizontal positions of player 0,
player 1, missile 0, missile 1 and the ball (0-159, 0 is the leftmost
visible pixel).
"""
import struct

REC = 22
WIDTH = 160

# NUSIZ low three bits -> (horizontal size of each player copy, copies,
# spacing between the starts of the copies in pixels).  Missiles share the
# same copy pattern.
_NUSIZ = {
    0: (1, 1, 0),
    1: (2, 2, 16),
    2: (1, 2, 32),
    3: (2, 3, 16),
    4: (1, 2, 64),
    5: (2, 3, 32),
    6: (4, 1, 0),
    7: (2, 1, 0),
}
# NUSIZ bits 4-5 -> missile width in pixels.
_MISSILE_WIDTH = {0: 1, 1: 2, 2: 4, 3: 8}
# CTRLPF bits 3-4 -> ball width in pixels.
_BALL_WIDTH = {0: 1, 1: 2, 2: 4, 3: 8}

CTRLPF_PRIORITY = 0x04
CTRLPF_SCORE = 0x02
CTRLPF_REFLECT = 0x01
REFP_REFLECT = 0x08
ENA_ENABLE = 0x02


class TiaError(ValueError):
    pass


def cells(pf0, pf1, pf2):
    """The 20 playfield cells of the left half, left to right."""
    return ([pf0 >> (7 - i) & 1 for i in range(4)] + [pf1 >> (7 - i) & 1 for i in range(8)]
            + [pf2 >> (7 - i) & 1 for i in range(8)])


def playfield_line(pf0, pf1, pf2, reflect):
    """The playfield bitmap for one line: one bool per pixel, 0-159."""
    left = cells(pf0, pf1, pf2)
    if reflect:
        # Right half is the mirror image of the left half, reflected
        # around the seam between pixels 79 and 80.
        pattern = left + left[::-1]
    else:
        pattern = left + left
    return [bool(pattern[x // 4]) for x in range(WIDTH)]


def _stamp(plane, x0, width, color):
    """Paint a solid run, clipping at the visible edges (no wrap-around)."""
    for x in range(max(0, x0), min(WIDTH, x0 + width)):
        plane[x] = color


def draw_player(plane, pos, grp, nusiz, refl):
    """Draw one player with its NUSIZ copies, sizing and reflection."""
    if not grp:
        return
    size, copies, spacing = _NUSIZ[nusiz & 7]
    reflect = bool(refl & REFP_REFLECT)
    for c in range(copies):
        base = pos + c * spacing
        for b in range(8):
            # REFP reflects the player bitmap left-to-right.
            bit_index = b if reflect else 7 - b
            if (grp >> bit_index) & 1:
                _stamp(plane, base + b * size, size, True)


def draw_missile(plane, pos, ena, nusiz):
    """Draw a missile: NUSIZ copy pattern, width from NUSIZ bits 4-5."""
    if not (ena & ENA_ENABLE):
        return
    _, copies, spacing = _NUSIZ[nusiz & 7]
    width = _MISSILE_WIDTH[(nusiz >> 4) & 3]
    for c in range(copies):
        _stamp(plane, pos + c * spacing, width, True)


def draw_ball(plane, pos, ena, ctrl):
    """Draw the ball, width from CTRLPF bits 3-4."""
    if not (ena & ENA_ENABLE):
        return
    width = _BALL_WIDTH[(ctrl >> 3) & 3]
    _stamp(plane, pos, width, True)


def draw(rec):
    (bk, pf_col, p0_col, p1_col, ctrl, pf0, pf1, pf2, grp0, grp1,
     nusiz0, nusiz1, refp0, refp1, enam0, enam1, enabl,
     p0_pos, p1_pos, m0_pos, m1_pos, bl_pos) = rec[:REC]

    pf_bits = playfield_line(pf0, pf1, pf2, bool(ctrl & CTRLPF_REFLECT))
    pfpix = sum(pf_bits)

    # Object planes: a set of pixels each object covers.
    p0 = [False] * WIDTH
    p1 = [False] * WIDTH
    m0 = [False] * WIDTH
    m1 = [False] * WIDTH
    ball = [False] * WIDTH
    draw_player(p0, p0_pos, grp0, nusiz0, refp0)
    draw_player(p1, p1_pos, grp1, nusiz1, refp1)
    draw_missile(m0, m0_pos, enam0, nusiz0)
    draw_missile(m1, m1_pos, enam1, nusiz1)
    draw_ball(ball, bl_pos, enabl, ctrl)

    score = bool(ctrl & CTRLPF_SCORE)
    priority = bool(ctrl & CTRLPF_PRIORITY)
    out = [bk] * WIDTH
    for x in range(WIDTH):
        # TIA colour mux, high to low priority.
        if priority:
            if pf_bits[x] or ball[x]:
                # In score mode the playfield/ball colour differs per half.
                out[x] = (p0_col if score and x < 80
                          else p1_col if score
                          else pf_col)
            elif m0[x] or p0[x]:
                out[x] = p0_col
            elif m1[x] or p1[x]:
                out[x] = p1_col
            else:
                out[x] = bk
        else:
            if m0[x] or p0[x]:
                out[x] = p0_col
            elif m1[x] or p1[x]:
                out[x] = p1_col
            elif pf_bits[x] or ball[x]:
                out[x] = (p0_col if score and x < 80
                          else p1_col if score
                          else pf_col)
            else:
                out[x] = bk
    return out, pfpix


def runs(px):
    out = []
    for c in px:
        h = "%02x" % c
        if out and out[-1][0] == h:
            out[-1][1] += 1
        else:
            out.append([h, 1])
    return out


def parse(data):
    if len(data) < 8 or data[:6] != b"TIALOG":
        raise TiaError("not a scan line log")
    (n,) = struct.unpack_from("<H", data, 6)
    have = min(n, (len(data) - 8) // REC)
    colors = {}
    pfpix = 0
    first = last = None
    for y in range(have):
        px, k = draw(data[8 + y * REC:8 + (y + 1) * REC])
        pfpix += k
        for c in px:
            colors["%02x" % c] = colors.get("%02x" % c, 0) + 1
        if y == 0:
            first = runs(px)
        last = runs(px)
    return {"lines": n, "complete": have == n, "playfield_pixels": pfpix,
            "colors": {k: colors[k] for k in sorted(colors)}, "first_line": first, "last_line": last}
