import pathlib
import unittest
from fractions import Fraction

from ymkit.ym import YmError, parse

from dumps import dump, interleave, with_regs, ym3, ym5


class BasicTests(unittest.TestCase):
    def test_strings(self):
        out = parse(dump())
        self.assertEqual((out["format"], out["title"], out["author"], out["comment"]), ("YM6!", "Test", "Me", None))

    def test_end_marker(self):
        self.assertTrue(parse(dump())["end_marker"])

    def test_ym5_header(self):
        out = parse(ym5(12, clock=1773400, rate=60, loop=5, magic=b"YM5!"))
        self.assertEqual((out["format"], out["frames"], out["clock"], out["rate"], out["loop_frame"]),
                         ("YM5!", 12, 1773400, 60, 5))
        self.assertEqual((out["duration"], out["loop_seconds"]), ("1/5", "1/12"))

    def test_ym6_header(self):
        out = parse(ym5(500, clock=2000000, rate=50, loop=64, title=b"Wings of Death",
                        author=b"Jochen Hippel", comment=b"Converted by Leonard"))
        self.assertEqual((out["frames"], out["clock"], out["rate"], out["loop_frame"]), (500, 2000000, 50, 64))
        self.assertEqual((out["title"], out["author"], out["comment"]),
                         ("Wings of Death", "Jochen Hippel", "Converted by Leonard"))
        self.assertEqual((str(out["duration"]), str(out["loop_seconds"])), ("10", "32/25"))

    def test_sixty_hz(self):
        out = parse(ym5(300, rate=60, magic=b"YM5!"))
        self.assertEqual((out["rate"], str(out["duration"])), (60, "5"))

    def test_extra_header_is_skipped(self):
        out = parse(ym5(3, extra=bytes(7), title=b"T"))
        self.assertEqual((out["title"], out["frames"]), ("T", 3))

    def test_digidrums(self):
        data = ym5(3, drums=(bytes(800), bytes(1200)), title=b"Cybernoid", author=b"Jeroen Tel",
                   magic=b"YM5!")
        out = parse(data)
        self.assertEqual((out["digidrums"], out["digidrum_bytes"]), (2, 2000))
        self.assertEqual((out["title"], out["author"], out["comment"]), ("Cybernoid", "Jeroen Tel", None))

    def test_empty_strings_are_null(self):
        out = parse(ym5(1))
        self.assertEqual((out["title"], out["author"], out["comment"]), (None, None, None))

    def test_missing_end_marker(self):
        self.assertFalse(parse(ym5(2, end=False))["end_marker"])

    def test_envelope_writes(self):
        data = with_regs(4, r13=0xFF)
        for i in (0, 3):
            data[i * 16 + 13] = 0x0A
        self.assertEqual(parse(ym5(4, regs=data))["envelope_writes"], 2)

    def test_envelope_writes_interleaved(self):
        data = with_regs(4, r13=0xFF)
        for i in (1, 2):
            data[i * 16 + 13] = 0x0A
        self.assertEqual(parse(ym5(4, attrs=1, regs=interleave(data)))["envelope_writes"], 2)

    def test_tone_frames(self):
        data = with_regs(4, r7=0b111)
        data[16 + 7] = 0b110   # frame 1: tone A on
        data[2 * 16 + 7] = 0b100  # frame 2: tones A and B on
        out = parse(ym5(4, regs=data))
        self.assertEqual(out["tone_frames"], [2, 1, 0])

    def test_tone_frames_interleaved(self):
        data = with_regs(4, r7=0b111)
        data[16 + 7] = 0b110
        data[2 * 16 + 7] = 0b100
        out = parse(ym5(4, attrs=1, regs=interleave(data)))
        self.assertEqual(out["tone_frames"], [2, 1, 0])

    def test_first_tone_a(self):
        data = with_regs(2, r0=0x34, r1=0x01)
        out = parse(ym5(2, clock=2000000, regs=data))
        self.assertEqual(out["first_tone_a"], str(Fraction(2000000, 16 * 0x134)))
        self.assertIsNone(parse(ym5(2))["first_tone_a"])

    def test_ym3(self):
        data = with_regs(5, r7=0b110)
        regs = [data[f * 16 + r] for f in range(5) for r in range(14)]
        out = parse(ym3(5, regs))
        self.assertEqual((out["format"], out["frames"], out["clock"], out["rate"], out["loop_frame"]),
                         ("YM3!", 5, 2000000, 50, 0))
        self.assertEqual((out["title"], out["author"], out["comment"], out["end_marker"]),
                         (None, None, None, None))
        self.assertFalse(out["interleaved"])
        self.assertEqual(out["envelope_writes"], 5)
        self.assertEqual(out["tone_frames"], [5, 0, 0])

    def test_ym3b_loop_frame(self):
        out = parse(ym3(5, loop=3))
        self.assertEqual((out["format"], out["frames"], out["loop_frame"]), ("YM3b", 5, 3))
        self.assertEqual(str(out["loop_seconds"]), "3/50")

    def test_ym3b_large_loop_frame(self):
        out = parse(ym3(2, loop=0x12345678))
        self.assertEqual((out["frames"], out["loop_frame"]), (2, 0x12345678))

    def test_rejects_unknown(self):
        for data in (b"", b"YM4!", b"YM6!" + bytes(40), b"YM5!LeOnArD!" + bytes(5)):
            with self.assertRaises(YmError):
                parse(data)

    def test_rejects_short_frames(self):
        data = ym5(4)
        with self.assertRaises(YmError):
            parse(data[:-10])

    def test_rejects_short_interleaved_frames(self):
        data = ym5(4, attrs=1)
        with self.assertRaises(YmError):
            parse(data[:-10])

    def test_rejects_short_digidrum(self):
        data = ym5(2, drums=(bytes(10),))
        self.assertGreater(len(data), 48)
        with self.assertRaises(YmError):
            parse(data[:44])

    def test_frames_too_large(self):
        data = bytearray(ym5(2))
        data[12:16] = (1000).to_bytes(4, "big")
        with self.assertRaises(YmError):
            parse(bytes(data))

    def test_examples(self):
        root = pathlib.Path(__file__).resolve().parent.parent / "examples"
        wings = parse((root / "wings.ym").read_bytes())
        self.assertEqual((wings["format"], wings["frames"], wings["rate"], wings["loop_frame"]),
                         ("YM6!", 500, 50, 64))
        self.assertEqual((wings["title"], wings["author"]), ("Wings of Death", "Jochen Hippel"))
        self.assertTrue(wings["interleaved"])
        self.assertEqual((wings["envelope_writes"], wings["tone_frames"]), (166, [500, 500, 250]))
        cyb = parse((root / "cybernoid.ym").read_bytes())
        self.assertEqual((cyb["format"], cyb["frames"], cyb["clock"], cyb["rate"]), ("YM5!", 300, 1773400, 60))
        self.assertEqual((cyb["digidrums"], cyb["digidrum_bytes"]), (2, 2000))
        self.assertEqual((cyb["title"], cyb["author"]), ("Cybernoid", "Jeroen Tel"))
        self.assertEqual((cyb["duration"], cyb["loop_seconds"]), ("5", "0"))
        self.assertEqual((cyb["envelope_writes"], cyb["tone_frames"]), (0, [300, 0, 0]))
