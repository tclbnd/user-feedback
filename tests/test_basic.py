import struct
import unittest

from amiscr.screen import ScreenError, parse


def dump(c0, c2, w, h, regs, planes):
    words_per_row = (w + 15) // 16
    body = b""
    for plane in planes:
        if len(plane) != words_per_row * h:
            raise ValueError("each plane needs words_per_row * h words")
        body += struct.pack(">%dH" % (words_per_row * h), *plane)
    return (b"AMISCR" + struct.pack(">HHHH", c0, c2, w, h)
            + struct.pack(">32H", *regs) + body)


def regs32(*early):
    out = [0] * 32
    out[:len(early)] = early
    return out


class BasicTests(unittest.TestCase):
    def test_one_plane_screen(self):
        regs = [0x000, 0xFFF] + [0] * 30
        data = b"AMISCR" + struct.pack(">HHHH", 0x1200, 0, 16, 1) + struct.pack(">32H", *regs) + b"\xff\x00"
        out = parse(data)
        self.assertEqual(out["first_line"], [["#ffffff", 8], ["#000000", 8]])

    def test_not_a_dump(self):
        with self.assertRaises(ScreenError):
            parse(b"AMIXXX" + bytes(100))


class ModeTests(unittest.TestCase):
    ZERO_PLANE = [0]

    def test_mode_detection(self):
        z = [0]
        self.assertEqual(parse(dump(0x1200, 0, 16, 1, regs32(), [z]))["mode"], "normal")
        self.assertEqual(parse(dump(0x6200, 0, 16, 1, regs32(), [z] * 6))["mode"], "ehb")
        self.assertEqual(parse(dump(0x6a00, 0, 16, 1, regs32(), [z] * 6))["mode"], "ham6")
        self.assertEqual(parse(dump(0x6600, 0, 16, 1, regs32(), [z] * 6))["mode"], "dpf")

    def test_ham_takes_precedence_over_dpf(self):
        out = parse(dump(0x6c00, 0, 16, 1, regs32(), [self.ZERO_PLANE] * 6))
        self.assertEqual(out["mode"], "ham6")

    def test_dpf_takes_precedence_over_ehb(self):
        out = parse(dump(0x6600, 0, 16, 1, regs32(), [self.ZERO_PLANE] * 6))
        self.assertEqual(out["mode"], "dpf")

    def test_bad_plane_count(self):
        with self.assertRaises(ScreenError):
            parse(dump(0x7000, 0, 16, 1, regs32(), []))

    def test_incomplete_dump_keeps_mode_but_no_screen(self):
        out = parse(dump(0x6a00, 0, 16, 1, regs32(), [self.ZERO_PLANE] * 6)[:80])
        self.assertEqual(out["mode"], "ham6")
        self.assertFalse(out["complete"])
        self.assertIsNone(out["first_line"])
        self.assertIsNone(out["colors_used"])


class PlaneOrderTests(unittest.TestCase):
    def test_plane_one_is_least_significant_index_bit(self):
        # plane 1 set / plane 2 clear means index 1, never index 2
        out = parse(dump(0x2200, 0, 16, 1, regs32(0x000, 0x0f00, 0x00f0),
                         [[0xFFFF], [0x0000]]))
        self.assertEqual(out["first_line"], [["#ff0000", 16]])


class EhbTests(unittest.TestCase):
    def test_upper_32_colours_are_half_bright(self):
        planes = [[0xFFFF]] * 5 + [[0x8000]]  # x0 -> index 63, x1 -> index 31
        out = parse(dump(0x6200, 0, 16, 1, regs32(*([0] * 31 + [0x0fff])), planes))
        self.assertEqual(out["first_line"][0], ["#777777", 1])
        self.assertEqual(out["first_line"][1], ["#ffffff", 15])


class HamTests(unittest.TestCase):
    REGS = regs32(0x000, 0x0fff)  # COLOR00 black, COLOR01 white

    def _words(self, pixels):
        return [[sum(((v >> p) & 1) << (15 - x) for x, v in enumerate(pixels))]
                for p in range(6)]

    def test_register_load_and_blue_modify(self):
        # x0 = 0x00 load COLOR00; x1 = 0x1f modify blue to f
        planes = [[0x2000]] * 5 + [[0x0000]]
        out = parse(dump(0x6a00, 0, 16, 1, self.REGS, planes))
        self.assertEqual(out["first_line"][:2], [["#000000", 2], ["#0000ff", 1]])

    def test_red_and_green_modify_on_held_colour(self):
        # load white, modify red -> 3, then green -> 5
        out = parse(dump(0x6a00, 0, 16, 1, self.REGS, self._words([0x01, 0x23, 0x35])))
        self.assertEqual(out["first_line"][:3],
                         [["#ffffff", 1], ["#33ffff", 1], ["#3355ff", 1]])

    def test_hold_resets_at_each_scanline(self):
        planes = [[0x2000, 0x0000]] * 5 + [[0x0000, 0x0000]]
        out = parse(dump(0x6a00, 0, 16, 2, self.REGS, planes))
        self.assertEqual(out["first_line"][0], ["#000000", 2])
        self.assertEqual(out["last_line"][0], ["#000000", 16])


class DpfTests(unittest.TestCase):
    REGS = regs32(0x000, 0x0f00, *([0] * 6), 0x000, 0x00f0)  # COLOR01 red, COLOR09 green

    PLANES = [[0x9000], [0x5000], [0], [0], [0], [0]]  # x0 pf1, x1 pf2, x2 none, x3 both

    def test_playfields_transparency_and_pf1_in_front(self):
        out = parse(dump(0x6600, 0x0000, 16, 1, self.REGS, self.PLANES))
        self.assertEqual(out["first_line"][:4],
                         [["#ff0000", 1], ["#00ff00", 1], ["#000000", 1], ["#ff0000", 1]])

    def test_pf2pri_bit_swaps_front_playfield(self):
        out = parse(dump(0x6600, 0x0400, 16, 1, self.REGS, self.PLANES))
        self.assertEqual(out["first_line"][:4],
                         [["#ff0000", 1], ["#00ff00", 1], ["#000000", 1], ["#00ff00", 1]])

    def test_unrelated_bplcon2_bits_keep_pf1_in_front(self):
        out = parse(dump(0x6600, 0x0040, 16, 1, self.REGS, self.PLANES))
        self.assertEqual(out["first_line"][3], ["#ff0000", 1])


if __name__ == "__main__":
    unittest.main()
