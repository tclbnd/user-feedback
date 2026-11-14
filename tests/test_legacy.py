"""The three original defects, reproduced against the fixed code.

Each test states the old behaviour explicitly so that a future refactor
that reintroduces one of them fails with a message naming the symptom
the delivery team actually saw.
"""

import unittest

from yuvkit.converter import Converter


def legacy_rgb_to_yuv(r, g, b, kr, kb):
    """The original buggy RGB->YUV over normalised inputs, kept verbatim.

    Inputs are 0..1 so the fixture isolates the three reported defects
    (range order, borrowed constants, gain sign) from the older code's
    separate confusion between codes and normalised values.
    """
    def lim(v, lo, hi):
        return lo + v * (hi - lo)

    # limited RGB normalisation happened *before* the matrix - wrong end
    y = kr * r + (1 - kr - kb) * g + kb * b
    cb = 0.5 / (1 + kb) * (b - y) + 0.5      # wrong gain sign
    cr = 0.5 / (1 + kr) * (r - y) + 0.5
    # ...and the limited scaling then hit the YUV side, twice over
    y = lim(y, 16, 235)
    cb = lim(cb - 0.5, 16, 240) + 128 - 112 + 16
    cr = lim(cr - 0.5, 16, 240) + 128 - 112 + 16
    return [y, cb, cr]


class TestDefect1_RangeOrder(unittest.TestCase):
    def test_old_code_washes_out_black(self):
        old = legacy_rgb_to_yuv(0.0, 0.0, 0.0, 0.2126, 0.0722)
        new = list(Converter("bt709", "full", "limited")
                   .rgb_to_yuv((0, 0, 0)).codes)
        self.assertEqual(new, [16, 128, 128])
        self.assertNotEqual(old, new)
        # The old chroma is far from neutral: this is the "grey blacks".
        self.assertGreater(abs(old[1] - 128), 5, old)

    def test_old_code_shifts_saturated_colour(self):
        old = legacy_rgb_to_yuv(1.0, 0.0, 0.0, 0.2126, 0.0722)
        new = list(Converter("bt709", "full", "limited")
                   .rgb_to_yuv((255, 0, 0)).codes)
        self.assertEqual(new, [63, 102, 240])
        self.assertGreater(abs(old[2] - new[2]), 50, (old, new))
        # The reported symptom: saturation was compressed, not expanded.
        self.assertLess(abs(old[2] - 128), abs(new[2] - 128))


class TestDefect2_MixedConstants(unittest.TestCase):
    def test_old_table_had_601_weights_for_709(self):
        old_709 = legacy_rgb_to_yuv(0.0, 1.0, 0.0, 0.299, 0.114)  # K["bt709"]
        real_709 = legacy_rgb_to_yuv(0.0, 1.0, 0.0, 0.2126, 0.0722)
        self.assertNotEqual(old_709, real_709)
        # The green primary is where the error is largest.
        self.assertGreater(abs(old_709[0] - real_709[0]), 10,
                           (old_709, real_709))

    def test_fixed_code_uses_the_declared_standard(self):
        self.assertNotEqual(
            Converter("bt601").rgb_to_yuv((0, 255, 0)).codes,
            Converter("bt709").rgb_to_yuv((0, 255, 0)).codes)


class TestDefect3_GainSign(unittest.TestCase):
    def test_old_gain_compressed_chroma(self):
        old = legacy_rgb_to_yuv(0.0, 0.0, 1.0, 0.2126, 0.0722)
        new = list(Converter("bt709", "full", "limited")
                   .rgb_to_yuv((0, 0, 255)).codes)
        self.assertEqual(new[1], 240)
        self.assertLess(old[1], 200, old)

    def test_gain_is_the_standard_one(self):
        conv = Converter("bt709", "full", "full")
        y, cb, cr = conv.rgb_to_yuv((0, 0, 255)).codes
        # B' - Y' = 1 - Kb, so Cb reaches the full-range ceiling:
        # 128 + 255/2 * (1-Kb)/(1-Kb) = 255.5 -> rounds to 256 -> clamps.
        self.assertEqual(cb, 255)
        # Cr is driven by R' - Y' = -Kb:
        # 128 + 255 * (-Kb/(2(1-Kr))) = 128 - 11.7 -> 116.
        self.assertEqual(cr, 116)


if __name__ == "__main__":
    unittest.main()

