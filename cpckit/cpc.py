"""Amstrad CPC screen dumps (.scr): optional AMSDOS header, interleaved screen memory, mode 0 / 1 / 2 pixel layouts."""

TYPES = {0: "basic", 1: "protected", 2: "binary"}
# Pixels per byte, screen width in pixels and number of pens for each mode.
PIXELS_PER_BYTE = {0: 2, 1: 4, 2: 8}
WIDTHS = {0: 160, 1: 320, 2: 640}
PENS = {0: 16, 1: 4, 2: 2}
HEADER_SIZE = 128
SCREEN_SIZE = 16384
# The last character row only uses 80 of its 2048 bytes, so a dump may stop
# after 16336 bytes and still cover every displayed pixel.
MIN_SCREEN = 16336
POINTS = {"top_left": (0, 0), "row_1": (0, 1), "row_8": (0, 8), "middle": (0.5, 100), "bottom_right": (1, 199)}


class CpcError(ValueError):
    pass


def line_offset(y):
    """Screen memory offset of line y.

    The 16K screen is split into 8 blocks of 2048 bytes, one per line within a
    character row, each block holding 25 lines of 80 bytes.
    """
    return (y & 7) * 0x800 + (y >> 3) * 80


def pen(screen, mode, x, y):
    """Pen number of pixel (x, y): 0-15 in mode 0, 0-3 in mode 1, 0-1 in mode 2."""
    if mode not in PIXELS_PER_BYTE:
        raise CpcError("unknown screen mode %r" % (mode,))
    b = screen[line_offset(y) + x // PIXELS_PER_BYTE[mode]]
    if mode == 0:
        # Two pixels per byte; the left one is the high nibble.
        return b >> 4 if x % 2 == 0 else b & 0x0F
    if mode == 1:
        # Four pixels per byte; each uses one bit from each nibble.
        n = x % 4
        return ((b >> (7 - n)) & 1) << 1 | ((b >> (3 - n)) & 1)
    # Mode 2: eight pixels per byte, leftmost in bit 7.
    return (b >> (7 - x % 8)) & 1


def header_checksum(data):
    """AMSDOS header checksum: sum of the first 67 bytes, modulo 65536."""
    return sum(data[:67]) & 0xFFFF


def parse_header(data):
    """Return the AMSDOS header fields, or None if the first 128 bytes are not one.

    A header is only accepted when its stored checksum matches, so arbitrary
    screen data is never mistaken for a header.
    """
    if len(data) < HEADER_SIZE or header_checksum(data) != (data[67] | data[68] << 8):
        return None
    name = data[1:9].decode("latin-1").rstrip(" \0")
    extension = data[9:12].decode("latin-1").rstrip(" \0")
    kind = data[18] & 0x0F
    load = int.from_bytes(data[21:23], "little")
    length = int.from_bytes(data[64:67], "little")
    execute = int.from_bytes(data[26:28], "little")
    return {
        "user": data[0],
        "filename": name + "." + extension,
        "type": TYPES.get(kind, "type_%d" % kind),
        "load": "&%04X" % load,
        "length": length,
        "execute": "&%04X" % execute,
    }


def histogram(screen, mode):
    """Number of pixels drawn with each pen, for the 200 lines of the given mode."""
    hist = [0] * PENS[mode]
    for y in range(200):
        base = line_offset(y)
        row = screen[base:base + 80]
        if mode == 0:
            for b in row:
                hist[b >> 4] += 1
                hist[b & 0x0F] += 1
        elif mode == 1:
            for b in row:
                for n in range(4):
                    hist[((b >> (7 - n)) & 1) << 1 | ((b >> (3 - n)) & 1)] += 1
        else:
            for b in row:
                ones = bin(b).count("1")
                hist[0] += 8 - ones
                hist[1] += ones
    return hist


def parse(data):
    # A header is only possible when enough screen bytes follow it, so an
    # uncommon 16336-byte dump is never stripped of its first 128 bytes.
    header = parse_header(data) if len(data) >= HEADER_SIZE + MIN_SCREEN else None
    body = data[HEADER_SIZE:] if header is not None else data
    if len(body) < MIN_SCREEN:
        raise CpcError("screen data shorter than %d bytes" % MIN_SCREEN)
    screen = body[:SCREEN_SIZE].ljust(SCREEN_SIZE, b"\0")
    points = {}
    for mode in sorted(WIDTHS):
        width = WIDTHS[mode]
        points["mode%d" % mode] = {
            name: pen(screen, mode, int(fx * (width - 1)), y) for name, (fx, y) in POINTS.items()
        }
    result = {"amsdos": header, "screen_bytes": len(body), "points": points}
    for mode in sorted(WIDTHS):
        result["mode%d_pens" % mode] = histogram(screen, mode)
    return result
