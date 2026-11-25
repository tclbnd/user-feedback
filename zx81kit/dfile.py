"""ZX81 display file dumps (.dfl): the character set followed by the display file."""

CHARS = " ##########\"$:?()<>=+-*/;,.£0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
HALT = 0x76


class DisplayError(ValueError):
    pass


def parse(data):
    if len(data) < 6 + 512 + 1 or data[:6] != b"ZX81DF":
        raise DisplayError("not a display file dump")
    font = data[6:518]
    d = data[518:]
    if d[0] != HALT:
        raise DisplayError("display file does not start with HALT")
    rows = []
    pos = 1
    while len(rows) < 24 and pos + 33 <= len(d):
        rows.append(d[pos:pos + 32])
        pos += 33
    text = []
    ink = 0
    first = None
    for y in range(24):
        codes = list(rows[y]) if y < len(rows) else [0] * 32
        text.append("".join(CHARS[c] if c < 64 else "?" for c in codes).rstrip())
        for ry in range(8):
            line = ""
            for c in codes:
                b = font[c * 8 + ry] if c < 64 else 0
                line += "".join("1" if b >> (7 - i) & 1 else "0" for i in range(8))
            ink += line.count("1")
            if y == 0 and ry == 0:
                first = line
    return {"rows": len(rows), "collapsed": False, "text": text,
            "inverse_chars": 0, "ink_pixels": ink, "first_line": first}
