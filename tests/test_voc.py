import struct
import unittest
from fractions import Fraction

from vockit.voc import VocError, scan

from vocs import block, cont, extended, marker, newfmt, pcm16, pcm8, silence, sound, text, voc


class SoundTests(unittest.TestCase):
    def test_continuation_counts_all_chunks(self):
        out = scan(voc([
            sound(156, pcm8([0] * 100)),
            cont(pcm8([0] * 200)),
            silence(50, 156),
            sound(156, pcm8([0] * 50)),
        ]))
        # 100 + 200 + 50 silent + 50 samples at 10 kHz
        self.assertEqual(Fraction(out["duration"]), Fraction(400, 10000))

    def test_silence_block_is_off_by_one(self):
        # The block stores samples - 1, so one stored entry means one sample.
        out = scan(voc([silence(1, 156)]))
        self.assertEqual(Fraction(out["duration"]), Fraction(1, 10000))

    def test_u8_peak_is_centered_on_128(self):
        out = scan(voc([sound(156, pcm8([99, -99, 0]))]))
        self.assertEqual(out["peak"], 99)
        out = scan(voc([sound(156, bytes([0, 255]))]))
        self.assertEqual(out["peak"], 128)

    def test_s16_peak(self):
        out = scan(voc([sound(156, pcm16([1000, -30000]), pack=4)]))
        self.assertEqual(out["codec"], "pcm_s16")
        self.assertEqual(out["peak"], 30000)
        self.assertEqual(Fraction(out["duration"]), Fraction(2, 10000))

    def test_extended_stereo(self):
        # tc 59136 with the 1.20 extended formula gives 40 kHz.
        out = scan(voc([
            extended(59136, pack=0, stereo=True),
            sound(156, pcm8([1, -1] * 200)),   # 400 bytes -> 200 stereo frames
        ]))
        self.assertEqual(out["sample_rate"], 40000.0)
        self.assertEqual(out["channels"], 2)
        self.assertEqual(out["codec"], "pcm_u8")
        self.assertEqual(Fraction(out["duration"]), Fraction(200, 40000))
        self.assertEqual(out["peak"], 1)

    def test_extended_mono_s16(self):
        out = scan(voc([
            extended(59136, pack=4, stereo=False),
            sound(156, pcm16([100, -200, 50])),
        ]))
        self.assertEqual(out["sample_rate"], 40000.0)
        self.assertEqual(out["channels"], 1)
        self.assertEqual(out["codec"], "pcm_s16")
        self.assertEqual(Fraction(out["duration"]), Fraction(3, 40000))
        self.assertEqual(out["peak"], 200)

    def test_extended_format_reused_by_continuation(self):
        out = scan(voc([
            extended(59136, 0, True),
            sound(156, pcm8([0] * 400)),
            cont(pcm8([0] * 400)),
        ]))
        self.assertEqual(Fraction(out["duration"]), Fraction(400, 40000))

    def test_new_format_block(self):
        out = scan(voc([newfmt(22050, 16, 1, 4, pcm16([1000, -2000]))]))
        self.assertEqual(out["version"], "1.10")
        self.assertEqual(out["sample_rate"], 22050.0)
        self.assertEqual(out["channels"], 1)
        self.assertEqual(out["codec"], "pcm_s16")
        self.assertEqual(Fraction(out["duration"]), Fraction(2, 22050))
        self.assertEqual(out["peak"], 2000)

    def test_new_format_stereo_u8(self):
        out = scan(voc([newfmt(48000, 8, 2, 0, pcm8([7, -7] * 2))]))
        self.assertEqual(out["channels"], 2)
        self.assertEqual(out["peak"], 7)
        self.assertEqual(Fraction(out["duration"]), Fraction(2, 48000))

    def test_adpcm_durations_and_null_peak(self):
        out = scan(voc([sound(156, bytes(100), pack=1)]))
        self.assertEqual(out["codec"], "adpcm4")
        self.assertEqual(Fraction(out["duration"]), Fraction(200, 10000))
        self.assertIsNone(out["peak"])
        for pack, spb, name in ((2, 3, "adpcm26"), (3, 4, "adpcm2")):
            out = scan(voc([sound(156, bytes(100), pack=pack)]))
            self.assertEqual(out["codec"], name)
            self.assertEqual(Fraction(out["duration"]), Fraction(100 * spb, 10000))
            self.assertIsNone(out["peak"])

    def test_alaw_ulaw(self):
        for pack, name in ((6, "alaw"), (7, "ulaw")):
            out = scan(voc([sound(156, bytes(100), pack=pack)]))
            self.assertEqual(out["codec"], name)
            self.assertEqual(Fraction(out["duration"]), Fraction(100, 10000))
            self.assertIsNone(out["peak"])

    def test_repeat_blocks_are_not_expanded(self):
        out = scan(voc([
            block(6, struct.pack("<H", 999)),
            sound(156, pcm8([0] * 100)),
            block(7, b""),
        ]))
        self.assertEqual(Fraction(out["duration"]), Fraction(100, 10000))
        self.assertEqual(out["blocks"], {"1": 1, "6": 1, "7": 1})

    def test_peak_only_first_sound_format(self):
        out = scan(voc([
            sound(156, pcm8([5, -5])),
            sound(156, pcm16([30000, -30000]), pack=4),
        ]))
        self.assertEqual(out["codec"], "pcm_u8")
        self.assertEqual(out["peak"], 5)
        out = scan(voc([
            sound(156, pcm16([30000, -30000]), pack=4),
            sound(156, pcm8([127, -128])),
        ]))
        self.assertEqual(out["codec"], "pcm_s16")
        self.assertEqual(out["peak"], 30000)

    def test_markers_and_texts(self):
        out = scan(voc([
            text("hello world"), marker(7),
            sound(156, pcm8([0])),
            marker(9), text("second"),
        ]))
        self.assertEqual(out["markers"], [7, 9])
        self.assertEqual(out["texts"], ["hello world", "second"])

    def test_version_1_20(self):
        out = scan(voc([sound(156, pcm8([0]))], version=0x0114))
        self.assertEqual(out["version"], "1.20")

    def test_larger_header_size(self):
        out = scan(voc([sound(156, pcm8([0] * 10))], header_size=32))
        self.assertEqual(Fraction(out["duration"]), Fraction(10, 10000))

    def test_empty_voice(self):
        out = scan(voc([]))
        self.assertIsNone(out["sample_rate"])
        self.assertIsNone(out["codec"])
        self.assertIsNone(out["peak"])
        self.assertEqual(out["duration"], "0")
        self.assertEqual(out["blocks"], {})

    def test_errors(self):
        with self.assertRaises(VocError):
            scan(b"x" * 30)
        with self.assertRaises(VocError):
            scan(voc([cont(b"data")]))
        with self.assertRaises(VocError):
            scan(voc([block(99, b"xy")]))


if __name__ == "__main__":
    unittest.main()
