"""Amstrad CPC screen dumps (.scr): optional AMSDOS header, interleaved screen memory, mode 0 / 1 / 2 pixel layouts."""
HEADER_SIZE = 128
SCREEN_SIZE = 16384
# Short dumps (e.g. 16336 bytes) are accepted; what is missing is zero padding.
MIN_SCREEN_SIZE = SCREEN_SIZE - 48
LINE_BYTES = 80

MODE_WIDTHS = {0: 160, 1: 320, 2: 640}
PIXELS_PER_BYTE = {0: 2, 1: 4, 2: 8}
TYPES = {0: "basic", 1: "protected", 2: "binary"}


class CpcError(ValueError):
    pass


def line_offset(y):
    """Offset of screen line y: eight 2048-byte blocks, one per line of a character row, 80 bytes apart."""
    return (y & 7) * 0x800 + (y >> 3) * LINE_BYTES


def pen(screen, mode, x, y):
    """Pen number of pixel (x, y) in the given screen mode."""
    b = screen[line_offset(y) + x // PIXELS_PER_BYTE[mode]]
    if mode == 2:
        # One bit per pixel, most significant bit first.
        return (b >> (7 - x % 8)) & 1
    if mode == 1:
        # Two bits per pixel: the high bit sits in 7..4, the low bit in 3..0.
        n = x % 4
        return ((b >> (7 - n)) & 1) << 1 | ((b >> (3 - n)) & 1)
    # Mode 0: four bits per pixel, most significant bits first.
    n = x % 2
    return (((b >> (7 - n)) & 1) << 3 | ((b >> (5 - n)) & 1) << 2
            | ((b >> (3 - n)) & 1) << 1 | ((b >> (1 - n)) & 1))


def point_positions(mode):
    """Sample points for a mode: top left, first two rows, middle row and opposite corner."""
    width = MODE_WIDTHS[mode]
    return {
        "top_left": (0, 0),
        "row_1": (0, 1),
        "row_8": (0, 8),
        "middle": (int(0.5 * (width - 1)), 100),
        "bottom_right": (width - 1, 199),
    }


def checksum(data):
    """AMSDOS header checksum: sum of the first 67 bytes, truncated to 16 bits."""
    return sum(data[:0x43]) & 0xFFFF


def has_header(data):
    """Whether the first 128 bytes are an AMSDOS header, as decided by its checksum.

    A screen must still follow the header, so blank data whose checksum happens to
    match (an all-zero block sums to zero) is not mistaken for a header.
    """
    if len(data) < HEADER_SIZE + MIN_SCREEN_SIZE:
        return False
    return (data[0x43] | data[0x44] << 8) == checksum(data)


def parse_header(data):
    """The fields of the 128-byte AMSDOS header at the start of data."""
    name = data[0x01:0x09].decode("latin-1").rstrip()
    ext = data[0x09:0x0C].decode("latin-1").rstrip()
    kind = data[0x12] & 0x0F
    load = data[0x15] | data[0x16] << 8
    execute = data[0x1A] | data[0x1B] << 8
    length = data[0x40] | data[0x41] << 8 | data[0x42] << 16
    return {
        "user": data[0x00],
        "filename": name + "." + ext,
        "type": TYPES.get(kind, "type_%d" % kind),
        "load": "&%04X" % load,
        "execute": "&%04X" % execute,
        "length": length,
    }


def parse(data):
    data = bytes(data)
    header = None
    body = data
    if has_header(data):
        header = parse_header(data)
        body = data[HEADER_SIZE:]
    if len(body) < MIN_SCREEN_SIZE:
        raise CpcError("screen data shorter than %d bytes" % MIN_SCREEN_SIZE)
    screen = body[:SCREEN_SIZE].ljust(SCREEN_SIZE, b"\0")

    points = {}
    for mode in (0, 1, 2):
        points["mode%d" % mode] = {
            name: pen(screen, mode, x, y)
            for name, (x, y) in point_positions(mode).items()
        }

    hist = [0, 0, 0, 0]
    for y in range(200):
        for x in range(320):
            hist[pen(screen, 1, x, y)] += 1

    return {"amsdos": header, "screen_bytes": len(body), "points": points, "mode1_pens": hist}
