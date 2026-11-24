import struct
import unittest

from adxkit.adx import parse, prediction_coefficients

SIGNATURE = b"(c)CRI"


def make_header(version, copy_off, enc=3, block=18, bits=4, channels=1,
                rate=44100, total=1000, highpass=500, flags=0):
    head = bytearray(copy_off)
    head[0:2] = b"\x80\x00"
    struct.pack_into(">H", head, 2, copy_off)
    head[4] = enc
    head[5] = block
    head[6] = bits
    head[7] = channels
    struct.pack_into(">IIHBB", head, 8, rate, total, highpass, version, flags)
    head[copy_off - 2:copy_off + 4] = SIGNATURE
    return head


def make_frames(block, channels, count, scale=100, eos=True):
    body = bytearray()
    for _ in range(count):
        frame = bytearray(block * channels)
        for ch in range(channels):
            struct.pack_into(">H", frame, ch * block, scale)
        body += frame
    if eos:
        term = bytearray(block * channels)
        struct.pack_into(">H", term, 0, 0x8001)
        body += term
    return body


class HeaderTests(unittest.TestCase):
    def test_sample_rate_is_32bit(self):
        head = make_header(3, 0x40, rate=22050)
        out = parse(bytes(head))
        self.assertEqual(out["sample_rate"], 22050)
        self.assertEqual(out["duration"], "20/441")

    def test_zero_sample_rate_duration_is_none(self):
        head = make_header(3, 0x40, rate=0)
        out = parse(bytes(head))
        self.assertIsNone(out["duration"])

    def test_copyright_signature_offset(self):
        head = make_header(3, 0x40)
        out = parse(bytes(head))
        self.assertTrue(out["copyright_ok"])
        self.assertEqual(out["data_offset"], 0x40 + 4)


class LoopTests(unittest.TestCase):
    def test_version3_loop_at_0x14(self):
        head = make_header(3, 0x60)
        struct.pack_into(">IIIII", head, 0x14, 1, 100, 200, 900, 1800)
        out = parse(bytes(head) + make_frames(18, 1, 5))
        self.assertTrue(out["loop"])
        self.assertEqual((out["loop_start"], out["loop_end"]), (100, 900))
        self.assertEqual((out["loop_start_byte"], out["loop_end_byte"]), (200, 1800))

    def test_version4_loop_at_0x24(self):
        head = make_header(4, 0x80)
        struct.pack_into(">IIIII", head, 0x24, 1, 50, 60, 950, 1900)
        out = parse(bytes(head) + make_frames(18, 2, 5))
        self.assertTrue(out["loop"])
        self.assertEqual(out["loop_start"], 50)
        self.assertEqual(out["loop_end_byte"], 1900)

    def test_disabled_loop_reports_none(self):
        head = make_header(4, 0x80)
        out = parse(bytes(head) + make_frames(18, 1, 3))
        self.assertFalse(out["loop"])
        self.assertIsNone(out["loop_start"])
        self.assertIsNone(out["loop_end_byte"])

    def test_version5_encryption_and_loop(self):
        head = make_header(5, 0x90)
        struct.pack_into(">IIII", head, 0x14, 8, 1, 2, 3)
        struct.pack_into(">IIIII", head, 0x24, 1, 11, 22, 88, 176)
        out = parse(bytes(head) + make_frames(18, 1, 4))
        self.assertTrue(out["encrypted"])
        self.assertEqual(out["key_type"], 8)
        self.assertTrue(out["loop"])
        self.assertEqual(out["loop_start"], 11)

    def test_version5_new_style_encryption_at_0x24(self):
        head = make_header(5, 0xa0)
        struct.pack_into(">IIII", head, 0x24, 9, 1, 2, 3)
        struct.pack_into(">IIIII", head, 0x34, 1, 5, 6, 7, 8)
        out = parse(bytes(head) + make_frames(18, 1, 4))
        self.assertTrue(out["encrypted"])
        self.assertEqual(out["key_type"], 9)
        self.assertTrue(out["loop"])
        self.assertEqual(out["loop_end"], 7)

    def test_unencrypted_version5(self):
        head = make_header(5, 0x90)
        out = parse(bytes(head) + make_frames(18, 1, 2))
        self.assertFalse(out["encrypted"])
        self.assertIsNone(out["key_type"])


class FrameTests(unittest.TestCase):
    def test_samples_per_block_from_block_size(self):
        head = make_header(4, 0x60, block=36, bits=4)
        out = parse(bytes(head) + make_frames(36, 1, 4))
        self.assertEqual(out["samples_per_block"], 68)
        self.assertEqual(out["data_samples"], 4 * 68)

    def test_eight_bit_block(self):
        head = make_header(4, 0x60, block=20, bits=8)
        out = parse(bytes(head) + make_frames(20, 1, 3))
        self.assertEqual(out["samples_per_block"], 18)

    def test_stereo_frame_counting(self):
        head = make_header(4, 0x60, channels=2)
        out = parse(bytes(head) + make_frames(18, 2, 7))
        self.assertEqual(out["data_frames"], 7)
        self.assertEqual(out["data_samples"], 7 * 32)

    def test_eos_terminator_excluded(self):
        head = make_header(3, 0x40)
        out = parse(bytes(head) + make_frames(18, 1, 6, eos=True))
        self.assertEqual(out["data_frames"], 6)

    def test_partial_tail_ignored_without_eos(self):
        head = make_header(3, 0x40)
        body = make_frames(18, 1, 3, eos=False) + bytes(9)
        out = parse(bytes(head) + body)
        self.assertEqual(out["data_frames"], 3)

    def test_max_scale_and_silent_blocks(self):
        head = make_header(3, 0x40)
        body = bytearray()
        for scale in (0, 340, 0, 120):
            frame = bytearray(18)
            struct.pack_into(">H", frame, 0, scale)
            body += frame
        term = bytearray(18)
        struct.pack_into(">H", term, 0, 0x8001)
        out = parse(bytes(head) + bytes(body) + bytes(term))
        self.assertEqual(out["max_scale"], 340)
        self.assertEqual(out["silent_blocks"], 2)


class CoefficientTests(unittest.TestCase):
    def test_standard_and_exponential_have_coefficients(self):
        for enc in (3, 4):
            out = parse(bytes(make_header(4, 0x60, enc=enc)))
            self.assertIsNotNone(out["coefficients"])
            self.assertEqual(len(out["coefficients"]), 2)

    def test_preset_and_ahx_have_no_coefficients(self):
        for enc in (2, 0x10):
            out = parse(bytes(make_header(4, 0x60, enc=enc)))
            self.assertIsNone(out["coefficients"])

    def test_zero_inputs_return_none(self):
        self.assertIsNone(prediction_coefficients(0, 500))
        self.assertIsNone(prediction_coefficients(44100, 0))


if __name__ == "__main__":
    unittest.main()
