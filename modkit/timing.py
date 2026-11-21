"""Play-through duration following ProTracker playback rules."""
from fractions import Fraction

from .module import cell


def duration(m):
    """Seconds (Fraction) for one play-through.

    Rows cost `speed` ticks of 2.5/`tempo` seconds each.  Effect Fxx sets
    the speed (xx < 32) or tempo (xx >= 32), Bxx jumps to a position and
    Dxx breaks to a row of the next (or jumped-to) position.  Playback
    stops when a row would be played a second time or the position runs
    past the end of the song.
    """
    speed, tempo = 6, 125
    total = Fraction(0)
    visited = set()
    pos, row = 0, 0
    orders, song_length, channels = m["orders"], m["song_length"], m["channels"]
    while pos < song_length:
        if (pos, row) in visited:
            break
        visited.add((pos, row))
        pattern = orders[pos]
        jump = None
        brk = None
        for ch in range(channels):
            _sample, _period, effect, param = cell(m, pattern, row, ch)
            if effect == 0xF and param:
                if param < 0x20:
                    speed = param
                else:
                    tempo = param
            elif effect == 0xB:
                jump = param
            elif effect == 0xD:
                # Parameter is BCD: high nibble = tens, low nibble = ones.
                brk = (param >> 4) * 10 + (param & 0x0F)
        total += Fraction(speed * 5, tempo * 2)
        if jump is not None or brk is not None:
            pos = jump if jump is not None else pos + 1
            row = brk if brk is not None else 0
            if row >= 64:
                row = 0
        else:
            row += 1
            if row >= 64:
                row = 0
                pos += 1
    return total
