import unittest

from cpckit.cpc import CpcError, parse

BLANK = bytes(16384)
WIDTHS = {0: 160, 1: 320, 2: 640}


def offset(y):
    return (y % 8) * 0x800 + (y // 8) * 80


def encode(mode, pixel):
    """Screen memory holding pixel(x, y) -> pen, built independently of the decoder."""
    mem = bytearray(16384)
    for y in range(200):
        for x in range(WIDTHS[mode]):
            p = pixel(x, y)
            if mode == 2:
                mem[offset(y) + x // 8] |= (p & 1) << (7 - x % 8)
            elif mode == 1:
                n = x % 4
                mem[offset(y) + x // 4] |= (p >> 1 & 1) << (7 - n) | (p & 1) << (3 - n)
            else:
                n = x % 2
                for k, bit in enumerate((3, 2, 1, 0)):
                    mem[offset(y) + x // 2] |= (p >> bit & 1) << (7 - 2 * k - n)
    return bytes(mem)


def pixels(mode):
    if mode == 2:
        return lambda x, y: (x // 5 + y) % 2
    if mode == 1:
        return lambda x, y: (x // 3 + y) % 4
    return lambda x, y: (x + 2 * y) % 16


def amsdos_header(body_length=16336, name="MYGAME  ", ext="SCR", kind=2, load=0xC000, execute=0x0400):
    header = bytearray(128)
    header[1:9] = name.encode("latin-1")
    header[9:12] = ext.encode("latin-1")
    header[0x12] = kind
    header[0x15:0x17] = load.to_bytes(2, "little")
    header[0x1A:0x1C] = execute.to_bytes(2, "little")
    header[0x40:0x43] = body_length.to_bytes(3, "little")
    header[0x43:0x45] = (sum(header[:0x43]) & 0xFFFF).to_bytes(2, "little")
    return bytes(header)


class BasicTests(unittest.TestCase):
    def test_no_header(self):
        out = parse(BLANK)
        self.assertEqual((out["amsdos"], out["screen_bytes"]), (None, 16384))

    def test_blank_pens(self):
        self.assertEqual(parse(BLANK)["mode1_pens"], [64000, 0, 0, 0])

    def test_points_for_all_modes(self):
        for mode in (0, 1, 2):
            pixel = pixels(mode)
            out = parse(encode(mode, pixel))
            widths = WIDTHS
            points = out["points"]["mode%d" % mode]
            self.assertEqual(points, {
                "top_left": pixel(0, 0),
                "row_1": pixel(0, 1),
                "row_8": pixel(0, 8),
                "middle": pixel(int(0.5 * (widths[mode] - 1)), 100),
                "bottom_right": pixel(widths[mode] - 1, 199),
            })

    def test_every_pixel_round_trips(self):
        from cpckit.cpc import pen
        for mode in (0, 1, 2):
            pixel = pixels(mode)
            data = encode(mode, pixel)
            for y in range(200):
                for x in range(WIDTHS[mode]):
                    self.assertEqual(pen(data, mode, x, y), pixel(x, y), (mode, x, y))

    def test_mode1_histogram(self):
        pixel = pixels(1)
        hist = [0, 0, 0, 0]
        for y in range(200):
            for x in range(320):
                hist[pixel(x, y)] += 1
        self.assertEqual(parse(encode(1, pixel))["mode1_pens"], hist)

    def test_header_fields(self):
        out = parse(amsdos_header() + encode(1, pixels(1))[:16336])
        self.assertEqual(out["amsdos"], {
            "user": 0, "filename": "MYGAME.SCR", "type": "binary",
            "load": "&C000", "execute": "&0400", "length": 16336,
        })
        self.assertEqual(out["screen_bytes"], 16336)

    def test_header_body_is_not_offset(self):
        body = encode(0, pixels(0))
        plain = parse(body)
        headed = parse(amsdos_header(16384) + body)
        self.assertEqual(plain["points"], headed["points"])

    def test_bad_checksum_is_not_a_header(self):
        header = bytearray(amsdos_header(16384))
        header[0x43] ^= 0xFF
        out = parse(bytes(header) + BLANK)
        self.assertIsNone(out["amsdos"])
        self.assertEqual(out["screen_bytes"], 128 + 16384)

    def test_unknown_type(self):
        self.assertEqual(parse(amsdos_header(16384, kind=7) + BLANK)["amsdos"]["type"], "type_7")

    def test_short_dumps(self):
        self.assertEqual(parse(BLANK[:16336])["screen_bytes"], 16336)
        with self.assertRaises(CpcError):
            parse(BLANK[:16335])

