"""ZX81 display file dumps (.dfl): the character set followed by the display file."""

# The 64 codes of the ZX81 character set in ROM order: space, the ten block
# graphics (all reported as "#"), the punctuation, the digits and the capitals.
# Adding 128 shows the same character in inverse video; any other code is not
# a display character.
CHARS = " " + "#" * 10 + '"£$:?()><=+-*/;,.' + "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
HALT = 0x76
HALT_BYTE = bytes((HALT,))
INVERSE = 0x80
CHARS_END = 64
UNKNOWN = "?"


class DisplayError(ValueError):
    pass


def _decode(code):
    """Return (character, inverted) for one display file byte."""
    if 0 <= code < CHARS_END:
        return CHARS[code], False
    if INVERSE <= code < INVERSE + CHARS_END:
        return CHARS[code - INVERSE], True
    return UNKNOWN, False


def _glyph(font, code):
    """Return (eight glyph rows, inverted) for one display file byte."""
    if 0 <= code < CHARS_END:
        base, inverted = code, False
    elif INVERSE <= code < INVERSE + CHARS_END:
        base, inverted = code - INVERSE, True
    else:
        return bytes(8), False
    return font[base * 8:base * 8 + 8], inverted


def _split_rows(d):
    """Split the display file into rows on its HALT bytes.

    D_FILE begins with a HALT; each row that follows is any number of character
    codes ended by a HALT.  The screen is 24 rows of up to 32 columns, so a
    full screen has 32 codes per row, while a collapsed screen (as saved on a
    1K machine) has shorter rows and possibly fewer than 24 of them.
    """
    rows = []
    pos = 1
    while len(rows) < 24:
        end = d.find(HALT_BYTE, pos)
        if end < 0:
            break
        rows.append(d[pos:end])
        pos = end + 1
    return rows


def parse(data):
    if len(data) < 6 + 512 + 1 or data[:6] != b"ZX81DF":
        raise DisplayError("not a display file dump")
    font = data[6:518]
    d = data[518:]
    if d[0] != HALT:
        raise DisplayError("display file does not start with HALT")
    found = _split_rows(d)
    rows = len(found)
    screen = [found[y][:32] for y in range(rows)]
    screen += [b""] * (24 - rows)
    text = []
    inverse_chars = 0
    ink = 0
    first = None
    for y in range(24):
        codes = list(screen[y])
        chars = []
        for code in codes:
            char, inverted = _decode(code)
            chars.append(char)
            if inverted:
                inverse_chars += 1
        text.append("".join(chars).rstrip())
        for ry in range(8):
            line = ""
            for x in range(32):
                glyph, inverted = _glyph(font, codes[x] if x < len(codes) else 0)
                bits = glyph[ry]
                if inverted:
                    bits ^= 0xFF
                line += "".join("1" if bits >> (7 - i) & 1 else "0" for i in range(8))
            ink += line.count("1")
            if y == 0 and ry == 0:
                first = line
    collapsed = any(len(row) < 32 for row in found)
    return {"rows": rows, "collapsed": collapsed, "text": text,
            "inverse_chars": inverse_chars, "ink_pixels": ink,
            "first_line": first}
