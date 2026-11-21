import unittest

from hevckit.hevc import scan

from streams import clip, sps


class BasicTests(unittest.TestCase):
    def test_counts(self):
        out = scan(clip(sps(1280, 720), pictures=8))
        self.assertEqual((out["nal_units"]["VCL"], out["pictures"], out["nal_units"]["SPS"]), (8, 8, 1))

    def test_main10(self):
        s = scan(clip(sps(1280, 720, depth=(10, 10), profile=2)))["sps"][0]
        self.assertEqual((s["profile_idc"], s["bit_depth_luma"], s["coded_width"]), (2, 10, 1280))
