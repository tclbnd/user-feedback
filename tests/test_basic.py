import os
import unittest

from qlkit.screen import ScreenError, parse

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(HERE, os.pardir, "examples")


def dump(mode, memory):
    return b"QLSCR" + bytes([mode]) + memory


def row4(green, red):
    """One mode-4 line from a green-plane byte and a red-plane byte, tiled."""
    return bytes([green, red]) * 64


def example(name):
    with open(os.path.join(EXAMPLES, name), "rb") as fh:
        return fh.read()


class BasicTests(unittest.TestCase):
    def test_white_mode4_screen(self):
        out = parse(b"QLSCR\x04" + b"\xff" * 32768)
        self.assertEqual(out["histogram"][7], 512 * 256)

    def test_not_a_dump(self):
        with self.assertRaises(ScreenError):
            parse(b"QLXXX\x04")


class Mode4Tests(unittest.TestCase):
    def test_width(self):
        self.assertEqual(parse(dump(4, bytes(32768)))["width"], 512)

    def test_even_byte_is_green(self):
        # green plane set only -> green (4)
        out = parse(dump(4, row4(0xFF, 0x00) * 256))
        self.assertEqual(out["histogram"], [0, 0, 0, 0, 512 * 256, 0, 0, 0])
        self.assertEqual(out["first_line"], "4" * 512)

    def test_odd_byte_is_red(self):
        out = parse(dump(4, row4(0x00, 0xFF) * 256))
        self.assertEqual(out["histogram"], [0, 0, 512 * 256, 0, 0, 0, 0, 0])
        self.assertEqual(out["first_line"], "2" * 512)

    def test_overlap_is_white(self):
        out = parse(dump(4, row4(0xFF, 0xFF) * 256))
        self.assertEqual(out["first_line"], "7" * 512)

    def test_alternating_fields(self):
        # green 0x33, red 0x55 like examples/menu.qls: 0,2,4,7 repeated.
        out = parse(dump(4, row4(0x33, 0x55) * 256))
        self.assertEqual(out["first_line"], "0247" * 128)

    def test_no_flash_bits(self):
        out = parse(dump(4, row4(0xFF, 0xFF) * 256))
        self.assertEqual(out["flash_bits"], 0)


class Mode8Tests(unittest.TestCase):
    def test_width(self):
        self.assertEqual(parse(dump(8, bytes(32768)))["width"], 256)

    def test_two_pixels_per_byte_high_nibble_first(self):
        # 0x10 = nibbles 1 (blue), 0 (black)
        out = parse(dump(8, bytes([0x10]) * 128 * 256))
        self.assertEqual(out["width"], 256)
        self.assertEqual(out["first_line"], "10" * 128)
        self.assertEqual(out["histogram"], [256 * 128, 256 * 128, 0, 0, 0, 0, 0, 0])

    def test_every_colour(self):
        out = parse(dump(8, bytes([0x01, 0x23, 0x45, 0x67]) * 32 * 256))
        self.assertEqual(out["first_line"], "01234567" * 32)
        self.assertEqual(out["colors_used"], 8)

    def test_flash_bit_counted_not_displayed(self):
        # 0x8F: high nibble flash + colour 0, low nibble flash + colour 7
        out = parse(dump(8, bytes([0x8F]) * 128 * 256))
        self.assertEqual(out["first_line"], "07" * 128)
        self.assertEqual(out["histogram"][0], 128 * 256)
        self.assertEqual(out["histogram"][7], 128 * 256)
        self.assertEqual(out["flash_bits"], 256 * 256)

    def test_flash_does_not_change_colour(self):
        plain = parse(dump(8, bytes([0x12]) * 128 * 256))
        flashed = parse(dump(8, bytes([0x9A]) * 128 * 256))
        self.assertEqual(plain["histogram"], flashed["histogram"])
        self.assertEqual(plain["first_line"], flashed["first_line"])
        self.assertEqual(plain["flash_bits"], 0)
        self.assertEqual(flashed["flash_bits"], 2 * 128 * 256)


class DumpTests(unittest.TestCase):
    def test_short_dump_keeps_whole_lines(self):
        out = parse(dump(8, bytes([0x00]) * (128 * 3 + 100)))
        self.assertEqual(out["lines"], 3)
        self.assertFalse(out["complete"])

    def test_full_dump_is_complete(self):
        out = parse(dump(4, bytes(32768)))
        self.assertEqual(out["lines"], 256)
        self.assertTrue(out["complete"])

    def test_menu_example(self):
        out = parse(example("menu.qls"))
        self.assertEqual(out["mode"], 4)
        self.assertEqual(out["width"], 512)
        self.assertTrue(out["complete"])
        self.assertEqual(out["histogram"], [100352, 0, 10240, 0, 10240, 0, 0, 10240])
        self.assertEqual(out["colors_used"], 4)
        self.assertEqual(out["flash_bits"], 0)
        self.assertEqual(out["first_line"], "0247" * 80 + "0" * 192)

    def test_game_example(self):
        out = parse(example("game.qls"))
        self.assertEqual(out["mode"], 8)
        self.assertEqual(out["width"], 256)
        self.assertTrue(out["complete"])
        self.assertEqual(out["histogram"], [47326, 3210, 6000, 3000, 3000, 0, 3000, 0])
        self.assertEqual(out["colors_used"], 6)
        self.assertEqual(out["flash_bits"], 12000)
        self.assertEqual(out["first_line"], "1013" * 30 + "0" * 136)
