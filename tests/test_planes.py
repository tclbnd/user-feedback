"""Planar (whole image) conversion, including 4:2:0 sub-sampling."""

import random
import unittest

from yuvkit.chroma import get_layout
from yuvkit.metrics import banding_profile
from yuvkit.planes import (luma_only_roundtrip, planes_to_rgb, planes_to_yuv,
                           roundtrip_planes)


def grey_ramp_image(width=64, height=8):
    return [[(v, v, v) for v in range(width)] for _ in range(height)]


def random_image(width=16, height=8, seed=5):
    rng = random.Random(seed)
    return [[[rng.randrange(256) for _ in range(3)] for _ in range(width)]
            for _ in range(height)]


class TestPlanarForward(unittest.TestCase):
    def test_plane_sizes_444(self):
        image = grey_ramp_image(16, 4)
        y, cb, cr, info = planes_to_yuv(image, layout="4:4:4")
        self.assertEqual((len(y), len(y[0])), (4, 16))
        self.assertEqual((len(cb), len(cb[0])), (4, 16))
        self.assertEqual(info["layout"]["name"], "4:4:4")

    def test_plane_sizes_420(self):
        image = grey_ramp_image(16, 4)
        y, cb, cr, info = planes_to_yuv(image, layout="4:2:0")
        self.assertEqual((len(y), len(y[0])), (4, 16))
        self.assertEqual((len(cb), len(cb[0])), (2, 8))
        self.assertEqual(info["layout"]["phase"], [0.5, 0.5])

    def test_plane_sizes_422(self):
        image = grey_ramp_image(16, 4)
        _y, cb, _cr, _info = planes_to_yuv(image, layout="4:2:2")
        self.assertEqual((len(cb), len(cb[0])), (4, 8))

    def test_info_carries_range_tags(self):
        image = grey_ramp_image(8, 2)
        _y, _cb, _cr, info = planes_to_yuv(image, "bt601", "full", "limited")
        tags = info["range_tags"]
        self.assertEqual(tags["standard"], "bt601")
        self.assertEqual(tags["yuv_range"], "limited")
        self.assertEqual(info["clipped"], 0)


class TestPlanarRoundtrip(unittest.TestCase):
    def test_444_roundtrip(self):
        image = grey_ramp_image(64, 4)
        back, report = roundtrip_planes(image, layout="4:4:4")
        self.assertLessEqual(report["max_err"], 2)
        # 4:4:4 carries chroma untouched, so only rounding remains.
        self.assertLessEqual(report["reconstructed_luma_max_err"], 1)

    def test_420_roundtrip_on_smooth_content(self):
        image = grey_ramp_image(64, 8)
        back, report = roundtrip_planes(image, layout="4:2:0")
        self.assertLessEqual(report["max_err"], 2)

    def test_subsampling_does_not_touch_the_y_plane(self):
        image = random_image()
        for layout in ("4:4:4", "4:2:2", "4:2:0", "4:2:0-left"):
            _err, report = luma_only_roundtrip(image, layout=layout)
            self.assertEqual(report["y_plane_max_err"], 0, layout)
        # ...while the luma recomputed from reconstructed RGB may move.
        # On *uniform random* content the chroma is white noise, so this
        # is the worst case any sub-sampler can face; the point of the
        # assertion is that centre siting (interpolating) is never worse
        # than left siting (replicating), which is the phase convention
        # the delivery pipeline depends on.
        _err, centre = luma_only_roundtrip(image, layout="4:2:0")
        _err, left = luma_only_roundtrip(image, layout="4:2:0-left")
        self.assertLess(centre["reconstructed_max_err"],
                        left["reconstructed_max_err"])
        self.assertLessEqual(centre["reconstructed_max_err"], 16)

    def test_centre_siting_beats_left_siting_on_random_content(self):
        image = random_image(width=32, height=8, seed=9)
        _c, centre = roundtrip_planes(image, layout="4:2:0")
        _l, left = roundtrip_planes(image, layout="4:2:0-left")
        self.assertLess(centre["max_err"], left["max_err"])

    def test_ramp_gains_no_banding(self):
        image = grey_ramp_image(256, 8)
        back, report = roundtrip_planes(image, layout="4:2:0")
        before = banding_profile([p[0] for p in image[4]])
        after = banding_profile([p[0] for p in back[4]])
        self.assertEqual(after["reversals"], 0)
        self.assertEqual(after["excess_stalls"], 0)
        self.assertLessEqual(after["max_run"], before["max_run"] + 1)

    def test_worst_case_reported(self):
        image = random_image()
        _back, report = roundtrip_planes(image)
        worst = report["worst"]
        self.assertIsNotNone(worst)
        self.assertGreaterEqual(worst["err"], 0)
        self.assertEqual(len(worst["src"]), 3)

    def test_planes_to_rgb_shape(self):
        image = grey_ramp_image(8, 4)
        y, cb, cr, info = planes_to_yuv(image, layout="4:2:0")
        back, meta = planes_to_rgb(y, cb, cr, info["width"], info["height"],
                                   layout="4:2:0")
        self.assertEqual(len(back), 4)
        self.assertEqual(len(back[0]), 8)
        self.assertEqual(meta["range_tags"]["layout"], "4:2:0")

    def test_lut_path_matches_matrix_path(self):
        image = random_image(width=8, height=4, seed=2)
        for layout in ("4:4:4", "4:2:0"):
            a, _ra = roundtrip_planes(image, layout=layout, path="matrix")
            b, _rb = roundtrip_planes(image, layout=layout, path="lut")
            self.assertEqual(a, b, layout)


class TestLayoutMetadata(unittest.TestCase):
    def test_phases(self):
        self.assertEqual(get_layout("4:4:4").chroma_phase, (0, 0))
        self.assertEqual(get_layout("4:2:2").chroma_phase,
                         (get_layout("4:2:2").siting.h_phase, 0))
        self.assertEqual(get_layout("4:2:0").chroma_phase,
                         (get_layout("4:2:0").siting.h_phase,
                          get_layout("4:2:0").siting.v_phase))


if __name__ == "__main__":
    unittest.main()

