import unittest

from gbskit.gbs import parse

from rips import rip


class BasicTests(unittest.TestCase):
    def test_title(self):
        out = parse(rip())
        self.assertEqual((out["title"], out["songs"], out["data_bytes"]), ("Test", 3, 64))

    def test_version(self):
        self.assertEqual(parse(rip())["version"], 1)

    def test_first_song_uses_header_value(self):
        self.assertEqual(parse(rip(first_song=1))["first_song"], 1)
        self.assertEqual(parse(rip(first_song=2))["first_song"], 2)

    def test_addresses_are_little_endian(self):
        out = parse(rip(load=0x0470, init=0x0480, play=0x04A0, stack=0xDFFF))
        self.assertEqual((out["load_address"], out["init_address"],
                          out["play_address"], out["stack_pointer"]),
                         ("$0470", "$0480", "$04A0", "$DFFF"))
        self.assertTrue(out["load_ok"])

    def test_vblank_when_timer_disabled(self):
        out = parse(rip(tac=0x80, tma=0xBE))     # double-speed flag set, but timer off
        self.assertEqual(out["timing"], "vblank")
        self.assertFalse(out["double_speed"])
        self.assertEqual(out["play_rate"], "262144/4389")

    def test_timer_rate_normal_speed(self):
        # TAC 0x05: enabled, clock select 1 = CPU/256 = 16384 Hz; TMA 0 -> 256 counts
        out = parse(rip(tac=0x05, tma=0))
        self.assertEqual(out["timing"], "timer")
        self.assertFalse(out["double_speed"])
        self.assertEqual(out["play_rate"], "64")

    def test_timer_rate_double_speed(self):
        # TAC 0x86: double speed, enabled, clock select 2 = CPU/64 = 65536 Hz (x2); TMA 0xBE -> 66 counts
        out = parse(rip(tac=0x86, tma=0xBE))
        self.assertEqual(out["timing"], "timer")
        self.assertTrue(out["double_speed"])
        self.assertEqual(out["play_rate"], "65536/33")

    def test_timer_rate_matches_hardware_clocks(self):
        for select, clock in enumerate((4096, 16384, 65536, 262144)):
            out = parse(rip(tac=0x04 | select, tma=0x80))   # 256 - 128 = 128 counts
            self.assertEqual(out["play_rate"], str(clock // 128))

    def test_double_speed_doubles_rate(self):
        single = parse(rip(tac=0x05, tma=0))
        double = parse(rip(tac=0x85, tma=0))
        self.assertTrue(double["double_speed"])
        self.assertEqual(2 * parse_num(single["play_rate"]),
                         parse_num(double["play_rate"]))


def parse_num(text):
    numerator, _, denominator = text.partition("/")
    value = float(numerator)
    return value / float(denominator) if denominator else value


if __name__ == "__main__":
    unittest.main()
