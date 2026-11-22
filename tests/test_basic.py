import unittest
from fractions import Fraction

from s3mkit.s3m import parse

from modules import ins, module, n


class HeaderTests(unittest.TestCase):
    def test_header(self):
        out = parse(module("menu", orders=(0,), patterns=[None]))
        self.assertEqual((out["title"], out["tracker"], out["patterns"]), ("menu", "Scream Tracker", 1))

    def test_channels_skip_disabled(self):
        # 255 is unused; the 0x80 bit marks a disabled channel in other trackers.
        out = parse(module(channels=(0, 8, 0x80, 0x89, 255), orders=(0,), patterns=[None]))
        self.assertEqual(out["channels"], 2)

    def test_orders_skip_markers_and_stop_at_end(self):
        out = parse(module(orders=(0, 254, 1, 255, 0, 1), patterns=[None, None]))
        self.assertEqual(out["orders"], [0, 1])


class SampleTests(unittest.TestCase):
    def test_sample(self):
        out = parse(module(orders=(0,), instruments=[ins("kick", 300)], patterns=[None]))
        s = out["samples"][0]
        self.assertEqual((s["index"], s["name"], s["frames"]), (1, "kick", 300))

    def test_loop_length_is_end_minus_begin(self):
        out = parse(module(orders=(0,), instruments=[ins("k", 1000, loop=(200, 700))], patterns=[None]))
        s = out["samples"][0]
        self.assertEqual((s["loop"], s["loop_start"], s["loop_length"]), (True, 200, 500))

    def test_bits_and_stereo_flags(self):
        out = parse(module(orders=(0,), patterns=[None], instruments=[
            ins("mono16", 300, bits=16),
            ins("stereo8", 300, stereo=True),
            ins("stereo16", 300, bits=16, stereo=True),
        ]))
        self.assertEqual([(s["bits"], s["stereo"]) for s in out["samples"]],
                         [(16, False), (8, True), (16, True)])


class NoteTests(unittest.TestCase):
    def test_note_cut_and_empty_note_are_not_notes(self):
        grid = {(0, 0): (n(4, 0), 1, None, None, None), (1, 0): (254, 1, None, None, None),
                (2, 0): (255, 1, None, None, None), (3, 0): (0, 1, None, None, None)}
        out = parse(module(orders=(0,), patterns=[grid]))
        self.assertEqual(out["notes"], 2)


class DurationTests(unittest.TestCase):
    def test_break_row_is_decimal(self):
        # Cxx=0x10 means decimal "10" -> row 10 of the next position (not row 16).
        first = {(0, 0): (n(4, 0), 1, None, 3, 0x10)}
        second = {(20, 0): (None, None, None, 2, 0)}
        out = parse(module(orders=(0, 1), patterns=[first, second], speed=6, tempo=125))
        self.assertEqual(Fraction(out["duration"]), 12 * Fraction(6 * 5, 125 * 2))

    def test_jump_and_repeat_stop(self):
        grid = {(0, 0): (n(4, 0), 1, None, 2, 1)}
        out = parse(module(orders=(0,), patterns=[grid], speed=6, tempo=125))
        self.assertEqual(Fraction(out["duration"]), Fraction(6 * 5, 125 * 2))

    def test_speed_and_tempo_commands(self):
        grid = {(0, 0): (None, None, None, 1, 3), (1, 0): (None, None, None, 20, 150),
                (2, 0): (None, None, None, 3, 1)}
        out = parse(module(orders=(0,), patterns=[grid], speed=6, tempo=125))
        expected = Fraction(3 * 5, 125 * 2) + 2 * Fraction(3 * 5, 150 * 2)
        self.assertEqual(Fraction(out["duration"]), expected)
