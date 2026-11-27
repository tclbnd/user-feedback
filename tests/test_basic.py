import struct
import unittest
import zlib

from snkkit.wave import SnkError, parse, synth


def voice(regs):
    return parse(b"SNKWAVE\0" + bytes([1]) + bytes(regs))["voices"][0]


class BasicTests(unittest.TestCase):
    def test_one_voice(self):
        out = parse(b"SNKWAVE\0" + bytes([1]) + bytes(6))
        self.assertEqual(len(out["voices"]), 1)
        self.assertEqual(len(out["voices"][0]["wave"]), 16)

    def test_not_a_snapshot(self):
        with self.assertRaises(SnkError):
            parse(b"SNKWAVX\0" + bytes(7))

    def test_truncated_register_block(self):
        with self.assertRaises(SnkError):
            parse(b"SNKWAVE\0" + bytes([2]) + bytes(6))


class HardwareTests(unittest.TestCase):
    def test_frequency_register_layout(self):
        # F1 supplies the high six bits (position 6), F2 the low six bits;
        # the top two lines of each latch are unconnected and ignored.
        self.assertEqual(voice([0x01, 0x00, 0, 0, 0, 0])["frequency"], 0x040)
        self.assertEqual(voice([0x00, 0x01, 0, 0, 0, 0])["frequency"], 0x001)
        self.assertEqual(voice([0x28, 0x00, 0, 0, 0, 0])["frequency"], 0xA00)
        self.assertEqual(voice([0xFF, 0xFF, 0, 0, 0, 0])["frequency"], 0xFFF)

    def test_frequency_is_an_increment_not_a_divisor(self):
        low = voice([0x00, 0x01, 0, 0, 0, 0])
        high = voice([0x00, 0x3F, 0, 0, 0, 0])
        self.assertLess(low["frequency"], high["frequency"])
        self.assertLess(float(low["cycle_hz"]), float(high["cycle_hz"]))
        expected = 6000000 * low["frequency"] / (4096 * 16 * 256)
        self.assertEqual(low["cycle_hz"], "%.3f" % expected)

    def test_wave_nibble_order_and_signed_levels(self):
        # 0x18 -> high nibble 1 (step 0), low nibble 8 (step 1, mid-scale).
        v = voice([0, 0, 0x18, 0, 0, 0])
        self.assertEqual(v["wave"][0], (1 - 8) * 16)
        self.assertEqual(v["wave"][1], 0)
        self.assertTrue(all(-128 <= level <= 112 for level in v["wave"]))

    def test_second_half_is_silence_not_a_copy(self):
        v = voice([0, 0, 0xFF, 0xFF, 0xFF, 0xFF])
        self.assertEqual(v["wave"][:8], [112] * 8)
        self.assertEqual(v["wave"][8:], [0] * 8)

    def test_zero_increment_audible_dc_has_no_pitch(self):
        # freq 0 but step 0 is a non-zero level: audible constant, no pitch.
        v = voice([0xC0, 0x00, 0x00, 0, 0, 0])
        self.assertEqual(v["frequency"], 0)
        self.assertFalse(v["silent"])
        self.assertIsNone(v["cycle_hz"])
        self.assertEqual(v["pcm"]["peak"], 32768)
        self.assertEqual(v["pcm"]["sum"], -32768 * 512)

    def test_zero_increment_mid_scale_is_silent(self):
        v = voice([0x00, 0x00, 0x80, 0x80, 0x80, 0x80])
        self.assertEqual(v["frequency"], 0)
        self.assertTrue(v["silent"])
        self.assertIsNone(v["cycle_hz"])
        self.assertEqual(v["pcm"]["peak"], 0)
        self.assertEqual(v["pcm"]["sum"], 0)

    def test_synth_matches_integer_accumulator(self):
        wave = list(range(-128, 0, 16)) + [0] * 8
        for freq in (1, 255, 1024, 4095):
            acc = pos = 0
            ref = []
            for _ in range(512):
                ref.append(wave[pos] * 256)
                if (acc & 0xFFF) + freq >= 4096:
                    pos = (pos + 1) % 16
                acc = (acc + freq) & 0xFFFF
            self.assertEqual(synth(freq, wave), ref)

    def test_pcm_envelope_and_crc32(self):
        v = voice([0x00, 0x02, 0xFF, 0xFF, 0xFF, 0xFF])
        samples = synth(v["frequency"], v["wave"])
        blob = b"".join(struct.pack("<h", s) for s in samples)
        self.assertEqual(v["pcm"]["rate_hz"], "23437.5")
        self.assertEqual(v["pcm"]["samples"], 512)
        self.assertEqual(v["pcm"]["crc32"], "%08x" % zlib.crc32(blob))
        self.assertEqual(v["pcm"]["peak"], max(abs(s) for s in samples))
        self.assertEqual(v["pcm"]["sum"], sum(samples))


if __name__ == "__main__":
    unittest.main()
