"""Small CD+G files for the tests."""


def packet(inst, data=(), top2=0):
    """One graphics packet; top2 sets the P and Q bits the way a ripper would."""
    d = list(data) + [0] * (16 - len(data))
    return bytes([0x09 | (top2 << 6), inst | (top2 << 6), top2 << 6, top2 << 6]
                 + [b | (top2 << 6) for b in d] + [top2 << 6] * 4)


def preset(c=0, top2=0):
    return packet(1, [c, 0], top2)


def tile(row, col, c0, c1, top2=0):
    return packet(6, [c0, c1, row, col] + [0x3F] * 12, top2)


def xor_tile(row, col, c0, c1, pattern=0x3F, top2=0):
    return packet(38, [c0, c1, row, col] + [pattern] * 12, top2)


def border(c, top2=0):
    return packet(2, [c], top2)


def colours(high, entries, top2=0):
    """load_colors_high (entries for 8..15) or low (0..7); entries are (r, g, b) 4-bit."""
    data = []
    for r, g, b in entries:
        data += [(r << 2) | (g >> 2), ((g & 0x03) << 4) | b]
    return packet(31 if high else 30, data, top2)


def transparent(c, top2=0):
    return packet(28, [c], top2)


def scroll_preset(h, v, c=0, top2=0):
    return packet(20, [c, h, v], top2)


def scroll_copy(h, v, top2=0):
    return packet(24, [h, v], top2)
