import struct
import unittest

from tiakit.playfield import TiaError, parse, runs

FIELDS = ("bk pf p0 p1 ctrl pf0 pf1 pf2 grp0 grp1 nusiz0 nusiz1 "
          "refp0 refp1 enam0 enam1 enabl x0 x1 xm0 xm1 xb").split()


def rec(**kw):
    regs = dict.fromkeys(FIELDS, 0)
    regs.update(kw)
    return bytes(regs[name] for name in FIELDS)


def line(**kw):
    return parse(b"TIALOG" + struct.pack("<H", 1) + rec(**kw))


class BasicTests(unittest.TestCase):
    def test_solid_wall(self):
        rec = bytes([0x00, 0x0E, 0, 0, 0, 0xF0, 0xFF, 0xFF]) + bytes(14)
        out = parse(b"TIALOG" + bytes([1, 0]) + rec)
        self.assertEqual(out["first_line"], [["0e", 160]])

    def test_not_a_log(self):
        with self.assertRaises(TiaError):
            parse(b"TIAXXX\x00\x00")

    def test_empty_log(self):
        out = parse(b"TIALOG" + bytes([0, 0]))
        self.assertEqual(out["lines"], 0)
        self.assertTrue(out["complete"])
        self.assertEqual(out["colors"], {})
        self.assertIsNone(out["first_line"])
        self.assertIsNone(out["last_line"])

    def test_truncated_log_keeps_whole_lines(self):
        data = b"TIALOG" + struct.pack("<H", 3) + rec() * 2 + bytes(10)
        out = parse(data)
        self.assertEqual(out["lines"], 3)
        self.assertFalse(out["complete"])


class PlayfieldTests(unittest.TestCase):
    def test_half_repeats_by_default(self):
        # PF1 covers cells 4-11; 0x0F lights its last four, so the same 16
        # pixels show up in both halves.
        out = line(pf=0x0E, pf1=0x0F)
        self.assertEqual(out["first_line"], [["00", 32], ["0e", 16], ["00", 64],
                                             ["0e", 16], ["00", 32]])

    def test_reflect_mirrors_the_right_half(self):
        # PF1 bit 7 is cell 4, i.e. pixels 16-19; its mirror lands at 140-143.
        out = line(pf=0x0E, ctrl=0x01, pf1=0x80)
        self.assertEqual(out["first_line"], [["00", 16], ["0e", 4], ["00", 120],
                                             ["0e", 4], ["00", 16]])

    def test_pf0_and_pf2_run_opposite_to_pf1(self):
        # PF0 bit 4 is the leftmost cell; PF2 bit 0 is the first cell after PF1.
        out0 = line(pf=0x0E, pf0=0x10, ctrl=0x01)
        self.assertEqual(out0["first_line"], [["0e", 4], ["00", 152], ["0e", 4]])
        out2 = line(pf=0x0E, pf2=0x01, ctrl=0x01)
        self.assertEqual(out2["first_line"], [["00", 48], ["0e", 4], ["00", 56],
                                              ["0e", 4], ["00", 48]])

    def test_playfield_pixels_count_covered_pixels(self):
        out = line(pf=0x0E, pf0=0xF0, pf1=0xFF, pf2=0xFF)
        self.assertEqual(out["playfield_pixels"], 160)
        self.assertEqual(out["first_line"], [["0e", 160]])

    def test_score_mode_colours_each_half(self):
        out = line(pf0=0xF0, pf1=0xFF, pf2=0xFF, ctrl=0x02, p0=0x45, p1=0x87)
        self.assertEqual(out["first_line"], [["45", 80], ["87", 80]])

    def test_playfield_priority_bit_swaps_ball_and_playfield(self):
        # Score mode gives the left playfield COLUP0, so a ball in COLUPF shows
        # in front of it; the priority bit drops the ball behind the playfield.
        regs = dict(pf0=0xF0, pf1=0xFF, pf2=0xFF, p0=0x45, p1=0x87,
                    pf=0x33, enabl=2, xb=0)
        front = line(ctrl=0x02, **regs)
        self.assertEqual(front["first_line"], [["33", 1], ["45", 79], ["87", 80]])
        behind = line(ctrl=0x06, **regs)
        self.assertEqual(behind["first_line"], [["45", 80], ["87", 80]])

    def test_players_cover_playfield_either_way(self):
        regs = dict(pf0=0xF0, pf1=0xFF, pf2=0xFF, pf=0x0E, p0=0x45, grp0=0xFF)
        for ctrl in (0x00, 0x04):
            out = line(ctrl=ctrl, **regs)
            self.assertEqual(out["first_line"], [["45", 8], ["0e", 152]], ctrl)


class ObjectTests(unittest.TestCase):
    def test_player_glyph_starts_at_its_position(self):
        out = line(p0=0x11, grp0=0x0F, x0=10)
        self.assertEqual(out["first_line"], [["00", 14], ["11", 4], ["00", 142]])

    def test_player_reflection(self):
        out = line(p0=0x11, grp0=0x0F, refp0=0x08, x0=10)
        self.assertEqual(out["first_line"], [["00", 10], ["11", 4], ["00", 146]])

    def test_player_copies(self):
        for nusiz, starts in [(1, [0, 16]), (2, [0, 32]),
                              (3, [0, 16, 32]), (4, [0, 64])]:
            out = line(p0=0x11, grp0=0x80, nusiz0=nusiz, x0=0)
            self.assertEqual(out["first_line"],
                             runs([0x11 if i in starts else 0x00
                                   for i in range(160)]), nusiz)

    def test_player_width_modes(self):
        narrow = line(p0=0x11, grp0=0x80, nusiz0=5, x0=0)
        self.assertEqual(narrow["first_line"], [["11", 2], ["00", 158]])
        wide = line(p0=0x11, grp0=0x80, nusiz0=7, x0=0)
        self.assertEqual(wide["first_line"], [["11", 4], ["00", 156]])

    def test_missile_enable_width_and_copies(self):
        self.assertEqual(line(p0=0x11, enam0=2, xm0=10)["first_line"],
                         [["00", 10], ["11", 1], ["00", 149]])
        self.assertEqual(line(p0=0x11, enam0=2, xm0=10, nusiz0=0x20)["first_line"],
                         [["00", 10], ["11", 4], ["00", 146]])
        self.assertEqual(line(p0=0x11, enam0=2, xm0=10, nusiz0=2)["first_line"],
                         [["00", 10], ["11", 1], ["00", 31], ["11", 1],
                          ["00", 117]])

    def test_missile_stays_dark_when_disabled(self):
        out = line(p0=0x11, xm0=10)
        self.assertEqual(out["first_line"], [["00", 160]])

    def test_ball_colour_and_width(self):
        self.assertEqual(line(pf=0x33, enabl=2, xb=5)["first_line"],
                         [["00", 5], ["33", 1], ["00", 154]])
        self.assertEqual(line(pf=0x33, enabl=2, xb=5, ctrl=0x10)["first_line"],
                         [["00", 5], ["33", 2], ["00", 153]])

    def test_objects_stack_player0_then_player1_then_ball(self):
        both = line(p0=0x11, p1=0x22, pf=0x33, grp0=0x80, grp1=0x80,
                    enabl=2, x0=5, x1=5, xb=5)
        self.assertEqual(both["first_line"], [["00", 5], ["11", 1], ["00", 154]])
        lower = line(p1=0x22, pf=0x33, grp1=0x80, enabl=2, x1=5, xb=5)
        self.assertEqual(lower["first_line"], [["00", 5], ["22", 1], ["00", 154]])

    def test_object_clipped_at_the_edge(self):
        out = line(p0=0x11, grp0=0xFF, x0=157)
        self.assertEqual(out["first_line"], [["00", 157], ["11", 3]])
        self.assertEqual(out["colors"], {"11": 3, "00": 157})
