import unittest
from fractions import Fraction

from ymkit.ym import YmError, parse

from dumps import ym3, ym5, ym6


def regs(frames, overrides=None):
    """A frames x 16 register matrix; overrides maps register number -> list (or scalar)."""
    matrix = [[0] * 16 for _ in range(frames)]
    for r, value in (overrides or {}).items():
        values = value if isinstance(value, (list, tuple)) else [value] * frames
        for f, v in enumerate(values):
            matrix[f][r] = v
    return matrix


class HeaderTests(unittest.TestCase):
    def test_ym6_big_endian_header(self):
        out = parse(ym6(frames=300, clock=2000000, rate=50, loop=64))
        self.assertEqual(out["frames"], 300)
        self.assertEqual(out["clock"], 2000000)
        self.assertEqual(out["rate"], 50)
        self.assertEqual(out["loop_frame"], 64)
        self.assertEqual(out["duration"], "6")
        self.assertEqual(out["loop_seconds"], str(Fraction(64, 50)))

    def test_ym5_big_endian_header(self):
        out = parse(ym5(frames=120, clock=1773400, rate=60))
        self.assertEqual((out["format"], out["frames"], out["clock"], out["rate"]),
                         ("YM5!", 120, 1773400, 60))
        # 120 frames at 60 fps is 2 seconds, not 120/50
        self.assertEqual(out["duration"], "2")

    def test_rate_comes_from_header_not_hardcoded_50(self):
        out = parse(ym6(frames=60, rate=60))
        self.assertEqual(out["rate"], 60)
        self.assertEqual(out["duration"], "1")

    def test_bad_signature_rejected(self):
        data = b"YM6!XXXXXXX!" + b"\0" * 40
        with self.assertRaises(YmError):
            parse(data)


class Ym3Tests(unittest.TestCase):
    def test_ym3_bare_frames(self):
        out = parse(ym3(10))
        self.assertEqual(out["format"], "YM3!")
        self.assertEqual(out["frames"], 10)
        self.assertEqual((out["clock"], out["rate"]), (2000000, 50))
        self.assertEqual(out["loop_frame"], 0)
        self.assertFalse(out["interleaved"])
        self.assertIsNone(out["end_marker"])
        self.assertIsNone(out["title"])
        # every YM3 frame writes envelope register 13
        self.assertEqual(out["envelope_writes"], 10)

    def test_ym3b_loop_frame_is_little_endian(self):
        out = parse(ym3(10, loop=7))
        self.assertEqual(out["format"], "YM3b")
        self.assertEqual(out["frames"], 10)
        self.assertEqual(out["loop_frame"], 7)
        self.assertEqual(out["loop_seconds"], str(Fraction(7, 50)))

    def test_ym3b_loop_value_used_not_file_size_guess(self):
        # 14*20 + 4 bytes: must be 20 frames with loop 3, not a 280/14 guess
        data = ym3(20, loop=3)
        self.assertEqual(len(data), 14 * 20 + 4)
        out = parse(data)
        self.assertEqual(out["frames"], 20)
        self.assertEqual(out["loop_frame"], 3)

    def test_ym3_register_order_is_frame_by_frame(self):
        # R0 holds the frame number in our builder; R7=0 means all tones on
        out = parse(ym3(4))
        self.assertEqual(out["tone_frames"], [4, 4, 4])

    def test_ym3_first_tone(self):
        # frame 0 R0 = 0 in builder -> period 0 -> null
        self.assertIsNone(parse(ym3(4))["first_tone_a"])
        # craft R0=44, R1=0 in frame 0 -> period 44
        data = bytearray(14 * 2)
        data[0] = 44
        out = parse(bytes(data))
        self.assertEqual(out["first_tone_a"], str(Fraction(2000000, 16 * 44)))

    def test_zero_frame_ym3(self):
        out = parse(b"")
        self.assertEqual(out["format"], "YM3!")
        self.assertEqual(out["frames"], 0)
        self.assertIsNone(out["first_tone_a"])
        self.assertEqual(out["tone_frames"], [0, 0, 0])

    def test_ym3b_loop_must_point_inside_the_tune(self):
        with self.assertRaises(YmError):
            parse(ym3(4, loop=4))

    def test_garbage_rejected(self):
        with self.assertRaises(YmError):
            parse(b"not an ym file at all")

    def test_truncated_modern_header_rejected(self):
        with self.assertRaises(YmError):
            parse(b"YM6!LeOnArD!" + b"\0" * 5)


class DigidrumTests(unittest.TestCase):
    def test_drums_then_strings_ym6(self):
        drums = (b"\x11" * 10, b"\x22" * 30)
        out = parse(ym6(4, drums=drums, title="Song", author="Artist", comment="note"))
        self.assertEqual(out["digidrums"], 2)
        self.assertEqual(out["digidrum_bytes"], 40)
        self.assertEqual((out["title"], out["author"], out["comment"]),
                         ("Song", "Artist", "note"))

    def test_drums_then_strings_ym5(self):
        out = parse(ym5(4, drums=(b"drum" * 5,), title="Tune", author="Ace"))
        self.assertEqual(out["digidrums"], 1)
        self.assertEqual(out["digidrum_bytes"], 20)
        self.assertEqual((out["title"], out["author"]), ("Tune", "Ace"))
        self.assertTrue(out["end_marker"])

    def test_drum_sizes_are_big_endian(self):
        # a 256-byte sample: LE parsing would read its size as 0
        sample = b"\xaa" * 256
        out = parse(ym6(2, drums=(sample,), title="S"))
        self.assertEqual(out["digidrum_bytes"], 256)
        self.assertEqual(out["title"], "S")
        self.assertEqual(out["frames"], 2)

    def test_truncated_drum_sample_rejected(self):
        good = ym6(2, drums=(b"x" * 8,))
        # cut into the 8-byte sample: the recorded size can no longer fit
        header_end = 34 + 4  # header plus the sample's 32-bit size word
        with self.assertRaises(YmError):
            parse(good[:header_end + 3])

    def test_truncated_drum_table_rejected(self):
        # drums=2 promised in the header, only one table entry present
        good = ym6(2, drums=(b"a", b"b"))
        second_size = 34 + 4 + 1
        with self.assertRaises(YmError):
            parse(good[:second_size])


class InterleaveTests(unittest.TestCase):
    def test_interleaved_envelope_writes(self):
        # R13: 0xFF means "do not write"; only 2 of 5 frames write
        r13 = [0xFF, 10, 0xFF, 12, 0xFF]
        out = parse(ym6(5, interleaved=True, regs=regs(5, {13: r13})))
        self.assertTrue(out["interleaved"])
        self.assertEqual(out["envelope_writes"], 2)

    def test_non_interleaved_envelope_writes(self):
        r13 = [0xFF, 10, 0xFF, 12, 0xFF]
        out = parse(ym6(5, interleaved=False, regs=regs(5, {13: r13})))
        self.assertFalse(out["interleaved"])
        self.assertEqual(out["envelope_writes"], 2)

    def test_interleaved_tone_frames(self):
        # mixer R7: bits 0..2 mute A..C when set; tone counts frames with the bit clear
        r7 = [0b000, 0b001, 0b011, 0b111]
        expected = [
            1,  # bit0 clear only in frame 0
            2,  # bit1 clear in frames 0,1
            3,  # bit2 clear in frames 0,1,2
        ]
        for interleaved in (True, False):
            out = parse(ym6(4, interleaved=interleaved, regs=regs(4, {7: r7})))
            self.assertEqual(out["tone_frames"], expected, interleaved)

    def test_interleaved_first_tone_uses_registers_0_and_1(self):
        matrix = regs(3, {0: [44, 9, 9], 1: [1, 9, 9]})  # period = (1<<8)|44 = 300
        out = parse(ym6(3, interleaved=True, clock=2000000, regs=matrix))
        self.assertEqual(out["first_tone_a"], str(Fraction(2000000, 16 * 300)))

    def test_non_interleaved_matches_interleaved_values(self):
        matrix = regs(4, {0: [10, 20, 30, 40], 1: [0, 0, 1, 1], 7: [0, 1, 2, 4],
                          13: [8, 0xFF, 9, 0xFF]})
        a = parse(ym6(4, interleaved=True, regs=matrix))
        b = parse(ym6(4, interleaved=False, regs=matrix))
        for key in ("envelope_writes", "tone_frames", "first_tone_a"):
            self.assertEqual(a[key], b[key])


class EndMarkerTests(unittest.TestCase):
    def test_missing_end_marker(self):
        data = ym6(2)[:-4]
        out = parse(data)
        self.assertFalse(out["end_marker"])

    def test_ym3_has_no_end_marker_field(self):
        self.assertIsNone(parse(ym3(3))["end_marker"])


if __name__ == "__main__":
    unittest.main()
