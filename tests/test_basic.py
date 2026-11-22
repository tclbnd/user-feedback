import unittest

from rstmkit.rstm import parse

from streams import brstm


class BasicTests(unittest.TestCase):
    def test_layout(self):
        out = parse(brstm(channels=2, total=50000))
        self.assertEqual((out["codec"], out["channels"], out["total_samples"], out["blocks"]), ("dsp-adpcm", 2, 50000, 4))

    def test_adpc(self):
        out = parse(brstm(channels=1, total=20000, tracks=[[0]]))
        self.assertEqual((out["has_adpc"], out["channels"], out["version"]), (True, 1, "1.0"))
