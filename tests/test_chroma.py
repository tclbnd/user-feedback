"""Chroma sub-sampling: siting, phase and invertibility on ramps."""

import unittest

from yuvkit.chroma import (CENTER, LEFT, downsample, get_layout,
                           planar_420_to_full, planar_to_420, upsample)
from yuvkit.metrics import banding_profile


class TestLayouts(unittest.TestCase):
    def test_known_layouts(self):
        self.assertEqual((get_layout("4:4:4").h_sub,
                          get_layout("4:4:4").v_sub), (1, 1))
        self.assertEqual((get_layout("4:2:2").h_sub,
                          get_layout("4:2:2").v_sub), (2, 1))
        self.assertEqual((get_layout("4:2:0").h_sub,
                          get_layout("4:2:0").v_sub), (2, 2))

    def test_aliases(self):
        self.assertEqual(get_layout("420").name, get_layout("4:2:0").name)
        self.assertEqual(get_layout("4-2-0").name, "4:2:0")
        self.assertIs(get_layout("center").siting, CENTER)
        self.assertIs(get_layout("left").siting, LEFT)

    def test_phase(self):
        self.assertEqual(get_layout("4:4:4").chroma_phase, (0, 0))
        self.assertEqual(get_layout("4:2:2").chroma_phase, (0.5, 0))
        self.assertEqual(get_layout("4:2:0").chroma_phase, (0.5, 0.5))
        self.assertEqual(get_layout("4:2:0-left").chroma_phase, (0, 0))

    def test_left_and_centre_are_distinct(self):
        self.assertNotEqual(get_layout("4:2:0").siting,
                            get_layout("4:2:0-left").siting)
        self.assertEqual(get_layout("4:2:0-left").name, "4:2:0-left")

    def test_unknown(self):
        with self.assertRaises(ValueError):
            get_layout("4:1:1")


class TestDownUp(unittest.TestCase):
    def test_444_is_identity(self):
        plane = [[10, 20, 30, 40], [50, 60, 70, 80]]
        form = get_layout("4:4:4")
        self.assertEqual(downsample(plane, form, "h"), plane)
        self.assertEqual(upsample(plane, form, "h", 4), plane)

    def test_422_centre_reproduces_linear_ramp(self):
        # A box average followed by the 1/4-3/4 blend is exact on a line.
        form = get_layout("4:2:2")
        plane = [[100 + 2 * x for x in range(64)] for _ in range(4)]
        down = downsample(plane, form, "h")
        self.assertEqual(len(down[0]), 32)
        up = upsample(down, form, "h", 64)
        for a, b in zip(plane, up):
            self.assertEqual(a, b)

    def test_422_left_reproduces_step_edge(self):
        form = get_layout("4:2:2-left")
        plane = [[100] * 4 + [200] * 4 for _ in range(2)]
        down = downsample(plane, form, "h")
        up = upsample(down, form, "h", 8)
        self.assertEqual(up[0][0], 100)
        self.assertEqual(up[0][7], 200)

    def test_420_roundtrip_bounded(self):
        form = get_layout("4:2:0")
        # A 4:2:0 filter pair cannot reproduce a wrapping sawtooth; use
        # a smooth ramp, which is what sub-sampling is designed for.
        src = [[64 + (x + y) for x in range(32)] for y in range(16)]
        down = downsample(downsample(src, form, "h"), form, "v")
        self.assertEqual(len(down), 8)
        self.assertEqual(len(down[0]), 16)
        back = upsample(upsample(down, form, "v", 16), form, "h", 32)
        err = max(abs(a - b) for ra, rb in zip(src, back)
                  for a, b in zip(ra, rb))
        # Two sub-sampled axes means two independent half-code rounding
        # steps (the intermediate chroma plane is 8-bit too), so the
        # budget is one code per axis rather than one in total.
        self.assertLessEqual(err, 2)

    def test_plane_helpers(self):
        cb = [[120] * 8 for _ in range(8)]
        cr = [[130] * 8 for _ in range(8)]
        dcb, dcr, form = planar_to_420(cb, cr)
        self.assertEqual((len(dcb), len(dcb[0])), (4, 4))
        bcb, bcr, form2 = planar_420_to_full(dcb, dcr, 8, 8)
        self.assertEqual(len(bcb), 8)
        self.assertEqual(form.name, form2.name)
        # a flat plane must survive sub-sampling untouched
        self.assertTrue(all(v == 120 for row in bcb for v in row))
        self.assertTrue(all(v == 130 for row in bcr for v in row))


class TestNoBanding(unittest.TestCase):
    def test_grey_ramp_has_no_added_plateaus(self):
        plane = [[128 + x // 2 for x in range(256)] for _ in range(4)]
        for layout in ("4:2:0", "4:2:2", "4:2:0-left"):
            form = get_layout(layout)
            down = downsample(plane, form, "h")
            up = upsample(down, form, "h", 256)
            before = banding_profile(plane[0])
            after = banding_profile(up[0])
            self.assertLessEqual(after["max_run"], before["max_run"] + 1,
                                 layout)
            self.assertEqual(after["reversals"], 0, layout)


if __name__ == "__main__":
    unittest.main()

