import unittest

from dtskit.dts import scan

from streams import frame


class BasicTests(unittest.TestCase):
    def test_single_frame(self):
        out = scan(frame(rate=24000, amode=9))
        self.assertEqual((out["layout"], out["frames"], out["sample_rate"], out["channels"]), ("BE16", 1, 24000, 5))

    def test_bitrate(self):
        self.assertEqual(scan(frame(kbps_index=24))["bitrate_kbps"], 1536)
