import unittest
from fractions import Fraction

from gbskit.gbs import CPU, FRAME_CLOCKS, parse

from rips import rip


class HeaderTests(unittest.TestCase):
    def test_addresses_are_little_endian(self):
        out = parse(rip(load=0x0470, init=0x0480, play=0x04A0, stack=0xDFFF, code=64))
        self.assertEqual(out["load_address"], "$0470")
        self.assertEqual(out["init_address"], "$0480")
        self.assertEqual(out["play_address"], "$04A0")
        self.assertEqual(out["stack_pointer"], "$DFFF")
        self.assertEqual(out["end_address"], "$04AF")

    def test_first_song_is_header_value(self):
        self.assertEqual(parse(rip(first=1))["first_song"], 1)
        self.assertEqual(parse(rip(first=5))["first_song"], 5)


class TimingTests(unittest.TestCase):
    def test_vblank_rate_when_timer_disabled(self):
        # TAC bit 2 clear: the timer interrupt is off even if other TAC bits are set.
        out = parse(rip(tma=0xBE, tac=0x82))
        self.assertEqual(out["timing"], "vblank")
        self.assertEqual(out["play_rate"], str(Fraction(CPU, FRAME_CLOCKS)))
        self.assertFalse(out["double_speed"])

    def test_tac_zero_is_vblank(self):
        out = parse(rip(tac=0))
        self.assertEqual(out["timing"], "vblank")
        self.assertEqual(Fraction(out["play_rate"]), Fraction(CPU, FRAME_CLOCKS))

    def test_timer_rate(self):
        # 65536 Hz timer clock, TMA 0xBE -> modulo 256 - 190 = 66.
        out = parse(rip(tma=0xBE, tac=0x06))
        self.assertEqual(out["timing"], "timer")
        self.assertEqual(Fraction(out["play_rate"]), Fraction(65536, 66))
        self.assertFalse(out["double_speed"])

    def test_timer_rate_double_speed_is_doubled(self):
        normal = parse(rip(tma=0xBE, tac=0x06))
        doubled = parse(rip(tma=0xBE, tac=0x86))
        self.assertEqual(doubled["timing"], "timer")
        self.assertTrue(doubled["double_speed"])
        self.assertEqual(Fraction(doubled["play_rate"]), Fraction(normal["play_rate"]) * 2)
        self.assertEqual(doubled["play_rate"], "65536/33")

    def test_timer_modulo_wraps_at_zero_tma(self):
        out = parse(rip(tma=0, tac=0x04))
        self.assertEqual(Fraction(out["play_rate"]), Fraction(4096, 256))

    def test_rate_is_a_fraction_string(self):
        # 4096 Hz / 256 counts = 16 calls per second, emitted as the string "16".
        self.assertEqual(parse(rip(tac=0x04, tma=0))["play_rate"], "16")
        self.assertEqual(parse(rip())["play_rate"], "262144/4389")


if __name__ == "__main__":
    unittest.main()
