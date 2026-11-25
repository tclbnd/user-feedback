import unittest

from tiakit.playfield import draw, parse, cells, playfield_line, TiaError


def rec(**kw):
    """Build a 22-byte line record with sensible zero defaults."""
    vals = dict(bk=0x00, pf=0x0e, p0=0x40, p1=0x80, ctrl=0x00,
                pf0=0, pf1=0, pf2=0, grp0=0, grp1=0,
                nusiz0=0, nusiz1=0, refp0=0, refp1=0,
                enam0=0, enam1=0, enabl=0,
                p0x=0, p1x=0, m0x=0, m1x=0, blx=0)
    vals.update(kw)
    return bytes([vals["bk"], vals["pf"], vals["p0"], vals["p1"],
                  vals["ctrl"], vals["pf0"], vals["pf1"], vals["pf2"],
                  vals["grp0"], vals["grp1"],
                  vals["nusiz0"], vals["nusiz1"], vals["refp0"], vals["refp1"],
                  vals["enam0"], vals["enam1"], vals["enabl"],
                  vals["p0x"], vals["p1x"], vals["m0x"], vals["m1x"], vals["blx"]])


def log(*records, n=None):
    n = len(records) if n is None else n
    return b"TIALOG" + bytes([n & 255, n >> 8]) + b"".join(records)


class PlayfieldTests(unittest.TestCase):
    def test_cells_order(self):
        # PF0 high nibble first, then PF1, then PF2, MSB first.
        self.assertEqual(cells(0xF0, 0x00, 0x00), [1, 1, 1, 1] + [0] * 16)
        self.assertEqual(cells(0x00, 0x80, 0x00), [0] * 4 + [1] + [0] * 15)
        self.assertEqual(cells(0x00, 0x00, 0x01), [0] * 19 + [1])

    def test_repeat_right_half(self):
        # CTRLPF bit0 clear: right half repeats the left pattern.
        line = playfield_line(0xF0, 0, 0, reflect=False)
        self.assertEqual(line[:16], [True] * 16)
        self.assertEqual(line[16:80], [False] * 64)
        self.assertEqual(line[80:96], [True] * 16)
        self.assertEqual(line[96:], [False] * 64)

    def test_mirror_right_half(self):
        # CTRLPF bit0 set: right half mirrors the left around the seam.
        line = playfield_line(0xF0, 0, 0, reflect=True)
        self.assertEqual(line[:16], [True] * 16)
        self.assertEqual(line[16:80], [False] * 64)
        self.assertEqual(line[80:144], [False] * 64)
        self.assertEqual(line[144:], [True] * 16)
        for x in range(80):
            self.assertEqual(line[x], line[159 - x])

    def test_score_mode_colors(self):
        r = rec(ctrl=0x02, bk=0x00, p0=0x45, p1=0x87, pf0=0xF0)
        px, k = draw(r)
        self.assertEqual(k, 32)  # PF bits counted regardless of color
        self.assertTrue(all(c == 0x45 for c in px[:16]))    # left PF -> COLUP0
        self.assertTrue(all(c == 0x87 for c in px[80:96]))  # right PF -> COLUP1
        self.assertTrue(all(c == 0x00 for c in px[16:80]))  # background

    def test_playfield_pixels_counted_under_objects(self):
        r = rec(pf0=0xF0, grp0=0xFF, p0x=0, p0=0x40, pf=0x0e)
        px, k = draw(r)
        self.assertEqual(k, 32)  # full left/right 4-cell walls still count
        self.assertTrue(all(c == 0x40 for c in px[:8]))    # player paints over PF


class PlayerTests(unittest.TestCase):
    def test_basic_player(self):
        r = rec(grp0=0x3C, p0x=10, p0=0x40)  # 00111100 -> lit at offsets 2..5
        px, _ = draw(r)
        self.assertEqual([px[x] for x in range(10, 18)],
                         [0, 0, 0x40, 0x40, 0x40, 0x40, 0, 0])

    def test_reflected_player(self):
        r = rec(grp0=0x3C, p0x=10, p0=0x40, refp0=0x08)  # mirror of 00111100
        px, _ = draw(r)
        self.assertEqual([px[x] for x in range(10, 18)],
                         [0, 0, 0x40, 0x40, 0x40, 0x40, 0, 0])

    def test_nusiz_double_width_two_copies(self):
        r = rec(grp0=0x81, nusiz0=0x01, p0x=10, p0=0x40)
        px, _ = draw(r)
        # two copies 16 apart, each pixel doubled
        self.assertEqual(px[10:14], [0x40, 0x40, 0, 0])
        self.assertEqual(px[26:30], [0x40, 0x40, 0, 0])  # second copy at 26

    def test_nusiz_three_copies_thirty_two_spacing(self):
        r = rec(grp0=0x80, nusiz0=0x05, p0x=10, p0=0x40)
        px, _ = draw(r)
        for base in (10, 42, 74):  # 10, 10+32, 10+64, doubled pixels
            self.assertEqual(px[base], 0x40)
            self.assertEqual(px[base + 1], 0x40)
            self.assertEqual(px[base + 2], 0)

    def test_nusiz_quad_size(self):
        r = rec(grp0=0x80, nusiz0=0x06, p0x=10, p0=0x40)
        px, _ = draw(r)
        self.assertEqual(px[10:14], [0x40] * 4)
        self.assertEqual(px[14], 0)

    def test_player_clipped_at_right_edge(self):
        r = rec(grp0=0xFF, nusiz0=0x06, p0x=150, p0=0x40)  # 32px at x150
        px, _ = draw(r)
        self.assertEqual(px[150:], [0x40] * 10)  # clipped, no wrap

    def test_player_zero_graphic_invisible(self):
        px, _ = draw(rec(grp0=0, p0x=0))
        self.assertTrue(all(c == 0 for c in px))


class MissileBallTests(unittest.TestCase):
    def test_missile_widths(self):
        for bits, width in ((0x00, 1), (0x10, 2), (0x20, 4), (0x30, 8)):
            px, _ = draw(rec(enam0=0x02, nusiz0=bits, m0x=20, p0=0x40))
            self.assertEqual(px[20:20 + width], [0x40] * width)
            self.assertEqual(px[20 + width], 0)

    def test_missile_copies(self):
        px, _ = draw(rec(enam0=0x02, nusiz0=0x02, m0x=10, p0=0x40))
        self.assertEqual(px[10], 0x40)
        self.assertEqual(px[11], 0)
        self.assertEqual(px[42], 0x40)  # second copy 32px later

    def test_missile_enable_bit(self):
        # only bit 1 of ENAM enables; 1 stays disabled, 3 enabled.
        self.assertTrue(all(c == 0 for c in draw(rec(enam0=0x01, m0x=5))[0]))
        self.assertEqual(draw(rec(enam0=0x03, m0x=5, p0=0x40))[0][5], 0x40)

    def test_missile_keeps_player_color_in_score_mode(self):
        px, _ = draw(rec(ctrl=0x02, enam0=0x02,
                         m0x=120, p0=0x45, p1=0x87))
        self.assertEqual(px[120], 0x45)  # missile 0 always COLUP0, even right half

    def test_ball_sizes(self):
        for bits, width in ((0x00, 1), (0x08, 2), (0x10, 4), (0x18, 8)):
            px, _ = draw(rec(enabl=0x02, ctrl=bits, blx=30, pf=0x0e))
            self.assertEqual(px[30:30 + width], [0x0e] * width)
            self.assertEqual(px[30 + width], 0)

    def test_ball_uses_playfield_color_and_mux(self):
        px, _ = draw(rec(enabl=0x02, blx=50, pf=0x0e, bk=0x00))
        self.assertEqual(px[50], 0x0e)
        # score mode: ball takes the half's playfield color
        px, _ = draw(rec(ctrl=0x02, enabl=0x02, blx=90, p0=0x45, p1=0x87))
        self.assertEqual(px[90], 0x87)


class PriorityTests(unittest.TestCase):
    def test_priority_playfield_over_players(self):
        r = rec(ctrl=0x04, pf0=0xF0, grp0=0xFF, p0x=20, p0=0x40, pf=0x0e, bk=0)
        px, _ = draw(r)
        self.assertTrue(all(c == 0x0e for c in px[:16]))    # wall wins over player
        self.assertTrue(all(c == 0x40 for c in px[20:28]))  # player visible off wall

    def test_normal_players_over_playfield(self):
        r = rec(pf0=0xF0, grp0=0xFF, p0x=0, p0=0x40, pf=0x0e)
        px, _ = draw(r)
        self.assertTrue(all(c == 0x40 for c in px[:8]))

    def test_player0_beats_player1(self):
        r = rec(grp0=0xFF, grp1=0xFF, p0x=0, p1x=0, p0=0x40, p1=0x80)
        px, _ = draw(r)
        self.assertTrue(all(c == 0x40 for c in px[:8]))

    def test_ball_behind_player_in_priority_mode(self):
        r = rec(ctrl=0x04, enabl=0x02, blx=5, grp0=0xFF, p0x=0,
                pf=0x0e, p0=0x40)
        px, _ = draw(r)
        self.assertEqual(px[5], 0x0e)  # ball/PF mux on top in priority mode


class ParseTests(unittest.TestCase):
    def test_complete_and_truncated(self):
        one = rec(pf0=0xF0)
        out = parse(log(one, one, one, n=3))
        self.assertTrue(out["complete"])
        self.assertEqual(out["lines"], 3)
        out = parse(log(one, one, n=5))  # header says 5, only 2 whole lines
        self.assertFalse(out["complete"])
        self.assertEqual(out["lines"], 5)
        self.assertEqual(out["playfield_pixels"], 2 * 32)

    def test_colors_sorted_hex_keys(self):
        out = parse(log(rec(bk=0x0A, pf=0xF0, pf0=0xF0)))
        self.assertEqual(list(out["colors"].keys()), sorted(out["colors"].keys()))
        self.assertTrue(all(len(k) == 2 and k == k.lower() for k in out["colors"]))

    def test_bad_magic(self):
        with self.assertRaises(TiaError):
            parse(b"TIAXXX\x00\x00")
