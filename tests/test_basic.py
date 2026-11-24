import struct
import unittest

from spukit.spu import SpuError, parse, slot

BLANK = bytes(51104)


def picture(palette_blocks, bitmap=None):
    """Build a .spu whose 199 palette blocks are `palette_blocks` and whose bitmap words are `bitmap`."""
    data = bytearray(51104)
    if bitmap is not None:
        data[:32000] = bitmap
    for y, words in enumerate(palette_blocks):
        data[32000 + y * 96:32000 + (y + 1) * 96] = struct.pack(">48H", *words)
    return bytes(data)


def block(index0):
    """A palette block with colour 0 set per section to `index0` and every other entry black."""
    words = [0] * 48
    words[0], words[16], words[32] = index0
    return words


WHITE = 0x777
RED = 0x700


class BasicTests(unittest.TestCase):
    def test_size(self):
        self.assertEqual(parse(BLANK)["bytes"], 51104)

    def test_wrong_size(self):
        with self.assertRaises(SpuError):
            parse(bytes(32034))

    def test_line_0_is_black(self):
        points = parse(picture([block((WHITE, WHITE, WHITE))] * 199))["points"]
        self.assertEqual(points["line_0"], "#000000")

    def test_palette_is_used_one_line_late(self):
        # Block 0 is white and the rest red: line 1 shows block 0, line 2 block 1.
        result = parse(picture([block((WHITE, WHITE, WHITE))] + [block((RED, RED, RED))] * 198))
        self.assertEqual(result["points"]["top_left"], "#ffffff")
        self.assertEqual(result["points"]["x1"], "#ffffff")
        # (160, 40) is line 40 and therefore block 39, i.e. red.
        self.assertEqual(result["points"]["x160"], "#ff0000")

    def test_three_sections_per_line(self):
        points = parse(picture([block((RED, 0x070, 0x007))] * 199))["points"]
        self.assertEqual(points["top_left"], "#ff0000")
        self.assertEqual(points["x160"], "#00ff00")
        self.assertEqual(points["x161"], "#00ff00")
        self.assertEqual(points["bottom_right"], "#0000ff")

    def test_section_boundaries(self):
        self.assertEqual(slot(159, 5), 5)
        self.assertEqual(slot(160, 5), 21)
        self.assertEqual(slot(239, 5), 21)
        self.assertEqual(slot(240, 5), 37)
        self.assertEqual(slot(319, 5), 37)

    def test_last_line_uses_last_block(self):
        blocks = [block((WHITE, WHITE, WHITE))] * 198 + [block((RED, RED, RED))]
        self.assertEqual(parse(picture(blocks))["points"]["bottom_right"], "#ff0000")

    def test_white_is_white(self):
        self.assertEqual(parse(picture([block((WHITE, WHITE, WHITE))] * 199))["points"]["center"], "#ffffff")

    def test_colors_shown_and_palette_changes(self):
        result = parse(picture([block((WHITE, WHITE, WHITE))] * 199))
        self.assertEqual(result["colors_shown"], 1)
        self.assertEqual(result["palette_changes"], 0)
        # Three sections with three different colours means three shown colours.
        result = parse(picture([block((RED, 0x070, 0x007))] * 199))
        self.assertEqual(result["colors_shown"], 3)
        # Switching the palette between blocks 99 and 100 changes line 100 only once.
        result = parse(picture([block((WHITE, WHITE, WHITE))] * 99 + [block((RED, RED, RED))] * 100))
        self.assertEqual(result["colors_shown"], 2)
        self.assertEqual(result["palette_changes"], 1)
