import unittest

from ymkit.ym import parse

from dumps import dump, ym3, ym5


def frame(regs=16, **values):
    out = [0] * regs
    for reg, value in values.items():
        out[int(reg[1:])] = value
    return out


class BasicTests(unittest.TestCase):
    def test_strings(self):
        out = parse(dump())
        self.assertEqual((out["format"], out["title"], out["author"], out["comment"]), ("YM6!", "Test", "Me", None))

    def test_end_marker(self):
        self.assertTrue(parse(dump())["end_marker"])

    def test_header_is_big_endian(self):
        out = parse(ym5([frame() for _ in range(300)], clock=1773400, rate=60, loop=128))
        self.assertEqual((out["frames"], out["clock"], out["rate"], out["loop_frame"]),
                         (300, 1773400, 60, 128))

    def test_rate_used_for_duration(self):
        out = parse(ym5([frame() for _ in range(120)], rate=60, loop=60))
        self.assertEqual((out["duration"], out["loop_seconds"]), ("2", "1"))

    def test_digidrums_skip_to_strings(self):
        out = parse(ym5([frame() for _ in range(2)], drums=[b"\1" * 8, b"\2" * 6], title=b"Song", author=b"Me"))
        self.assertEqual((out["digidrums"], out["digidrum_bytes"]), (2, 14))
        self.assertEqual((out["title"], out["author"]), ("Song", "Me"))

    def test_extra_header_bytes_skipped(self):
        self.assertEqual(parse(ym5([frame()], extra=b"xyz"))["title"], "T")

    def test_interleaved_register_order(self):
        frames = [frame(r7=0b11111000, r13=0xFF), frame(r7=0b11111100, r13=0x0A), frame(r7=0b11111111, r13=0xFF)]
        for interleaved in (False, True):
            out = parse(ym5(frames, interleaved=interleaved))
            self.assertTrue(out["interleaved"] == interleaved)
            self.assertEqual(out["tone_frames"], [2, 2, 1])
            self.assertEqual(out["envelope_writes"], 1)

    def test_tone_and_envelope(self):
        frames = [frame(r7=0b11111000, r13=0x0A), frame(r7=0b11111111, r13=0xFF)]
        out = parse(ym5(frames))
        self.assertEqual(out["envelope_writes"], 1)
        self.assertEqual(out["tone_frames"], [1, 1, 1])

    def test_first_tone_a(self):
        out = parse(ym5([frame(r0=0x34, r1=0x02)], clock=2000000))
        self.assertEqual(out["first_tone_a"], str(__import__("fractions").Fraction(2000000, 16 * 0x234)))
        self.assertIsNone(parse(ym5([frame(r0=0, r1=0)]))["first_tone_a"])

    def test_ym3(self):
        frames = [frame(14, r7=0b11111000, r13=0x08), frame(14, r7=0b11111111, r13=0x0F)]
        out = parse(ym3(frames))
        self.assertEqual((out["format"], out["frames"], out["clock"], out["rate"], out["loop_frame"]),
                         ("YM3!", 2, 2000000, 50, 0))
        self.assertEqual((out["title"], out["author"], out["comment"], out["end_marker"]),
                         (None, None, None, None))
        self.assertEqual(out["envelope_writes"], 2)
        self.assertEqual(out["tone_frames"], [1, 1, 1])

    def test_ym3b_loop_frame(self):
        out = parse(ym3([frame(14)], loop=37, magic=b"YM3b"))
        self.assertEqual((out["format"], out["frames"], out["loop_frame"]), ("YM3b", 1, 37))

    def test_not_ym(self):
        with self.assertRaises(ValueError):
            parse(b"YM2!" + bytes(64))
