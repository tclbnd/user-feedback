"""Atari 2600 scan line logs (.tia): rebuild what the TIA draws on each line.

Each log line holds the register values that stood for the whole scan line, so
a line can be rebuilt on its own: the playfield bits (one half, either repeated
or mirrored onto the other half), then player 0/1, missile 0/1 and the ball,
each at its logged position and drawn in front of or behind the playfield.
"""
import struct

REC = 22
VISIBLE = 160

# Byte layout of one line's record.
(BK, PF, P0, P1, CTRL, PF0, PF1, PF2, GRP0, GRP1, NUSIZ0, NUSIZ1,
 REFP0, REFP1, ENAM0, ENAM1, ENABL, X0, X1, XM0, XM1, XB) = range(REC)

CTRL_REFLECT = 0x01      # right half mirrors the left half instead of repeating it
CTRL_SCORE = 0x02        # left half uses COLUP0, right half uses COLUP1
CTRL_PRIORITY = 0x04     # playfield drawn in front of players, missiles and ball
CTRL_BALL_SHIFT = 4      # CTRLPF bits 4-5: ball width 1, 2, 4 or 8 pixels

ENABLE = 0x02            # ENAM0/ENAM1/ENABL bit 1 starts the object
REFLECT = 0x08           # REFP0/REFP1 bit 3 mirrors the player

# NUSIZ bits 0-2: (number of copies, spacing between their starts).  Modes 5
# and 7 widen the player instead of copying it.
COPIES = {0: (1, 0), 1: (2, 16), 2: (2, 32), 3: (3, 16),
          4: (2, 64), 5: (1, 0), 6: (3, 32), 7: (1, 0)}
PLAYER_WIDTH = {0: 1, 1: 1, 2: 1, 3: 1, 4: 1, 5: 2, 6: 1, 7: 4}
# NUSIZ bits 4-5 size a missile (1, 2, 4 or 8 pixels); the ball's width comes
# from the same two bits of CTRLPF.
MISSILE_WIDTH = {0: 1, 1: 2, 2: 4, 3: 8}
BALL_WIDTH = {0: 1, 1: 2, 2: 4, 3: 8}


class TiaError(ValueError):
    pass


def cells(pf0, pf1, pf2):
    """The 20 playfield cells of the left half, left to right.

    PF0 keeps its four cells in bits 4-7 (bit 4 leftmost) and PF2 in bits 0-7
    (bit 0 leftmost), so both run opposite to PF1's bits 7-0.
    """
    return ([pf0 >> (4 + i) & 1 for i in range(4)] + [pf1 >> (7 - i) & 1 for i in range(8)]
            + [pf2 >> i & 1 for i in range(8)])


def playfield(rec):
    """The 40 playfield cells of a whole line, left to right."""
    left = cells(rec[PF0], rec[PF1], rec[PF2])
    return left + (left[::-1] if rec[CTRL] & CTRL_REFLECT else left)


def _at(row, starts):
    """Pixels lit by ``row`` laid down at each of ``starts``, clipped to view."""
    out = set()
    for start in starts:
        for i, bit in enumerate(row):
            x = start + i
            if bit and 0 <= x < VISIBLE:
                out.add(x)
    return out


def player_pixels(grp, nusiz, refp, x):
    """Pixels lit by a player glyph, with its copies, width and reflection."""
    mode = nusiz & 0x07
    count, spacing = COPIES[mode]
    bits = [(grp >> (7 - i)) & 1 for i in range(8)]
    if refp & REFLECT:
        bits.reverse()
    row = [b for b in bits for _ in range(PLAYER_WIDTH[mode])]
    return _at(row, [x + i * spacing for i in range(count)])


def missile_pixels(nusiz, enabled, x):
    """Pixels lit by a missile; NUSIZ bits 4-5 size it and bits 0-2 copy it."""
    if not enabled:
        return set()
    row = [1] * MISSILE_WIDTH[(nusiz >> 4) & 0x03]
    count, spacing = COPIES[nusiz & 0x07]
    return _at(row, [x + i * spacing for i in range(count)])


def ball_pixels(ctrl, enabled, x):
    """Pixels lit by the ball; CTRLPF bits 4-5 set its width."""
    if not enabled:
        return set()
    return _at([1] * BALL_WIDTH[(ctrl >> CTRL_BALL_SHIFT) & 0x03], [x])


def draw(rec):
    """The 160 visible pixels of a line, and how many carry playfield bits."""
    ctrl = rec[CTRL]
    pf = playfield(rec)
    p0 = player_pixels(rec[GRP0], rec[NUSIZ0], rec[REFP0], rec[X0])
    p1 = player_pixels(rec[GRP1], rec[NUSIZ1], rec[REFP1], rec[X1])
    m0 = missile_pixels(rec[NUSIZ0], rec[ENAM0] & ENABLE, rec[XM0])
    m1 = missile_pixels(rec[NUSIZ1], rec[ENAM1] & ENABLE, rec[XM1])
    ball = ball_pixels(ctrl, rec[ENABL] & ENABLE, rec[XB])
    over = bool(ctrl & CTRL_PRIORITY)
    score = bool(ctrl & CTRL_SCORE)
    pf_colour = rec[PF] if not score else None

    out = []
    pfpix = 0
    for x in range(VISIBLE):
        on_pf = bool(pf[x // 4])  # 40 cells, 4 pixels each
        pfpix += on_pf
        if score:
            # Score mode splits the playfield's colour at the centre.
            shown = rec[P0] if x < VISIBLE // 2 else rec[P1]
        else:
            shown = pf_colour
        # Players and missiles always cover the playfield: player/missile 0
        # over player/missile 1.  Below them the ball and playfield swap places
        # when the priority bit is set; otherwise the ball is in front.
        if x in p0 or x in m0:
            colour = rec[P0]
        elif x in p1 or x in m1:
            colour = rec[P1]
        elif over and on_pf:
            colour = shown
        elif x in ball:
            colour = rec[PF]
        elif on_pf:
            colour = shown
        else:
            colour = rec[BK]
        out.append(colour)
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
