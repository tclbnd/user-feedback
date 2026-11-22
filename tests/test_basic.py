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

    def test_sample_rate_is_16_bit(self):
        for rate in (22050, 32000, 44100, 48000):
            self.assertEqual(parse(brstm(rate=rate))["sample_rate"], rate)

    def test_duration_uses_total_samples(self):
        out = parse(brstm(codec=1, rate=32000, total=4410))
        self.assertEqual(out["duration"], "441/3200")
        self.assertEqual(out["total_samples"], 4410)

    def test_pcm_codecs(self):
        self.assertEqual(parse(brstm(codec=0))["codec"], "pcm8")
        self.assertEqual(parse(brstm(codec=1))["codec"], "pcm16")

    def test_stale_loop_start_does_not_loop(self):
        out = parse(brstm(codec=1, total=8000, loop=None, stale_loop=4000))
        self.assertIsNone(out["loop"])

    def test_loop_block_and_scales(self):
        out = parse(brstm(channels=2, rate=32000, total=60000, loop=30000))
        self.assertEqual(out["loop"]["block"], 2)
        self.assertEqual(out["loop"]["start_seconds"], "15/16")
        self.assertEqual(out["loop"]["predictor_scale"], ["0x33", "0x34"])
        self.assertEqual(out["loop"]["history"], [[20, -20], [21, -21]])

    def test_tracks(self):
        out = parse(brstm(channels=4, tracks=[[0, 1], [2, 3]], track_type=1))
        self.assertEqual([t["channels"] for t in out["tracks"]], [[0, 1], [2, 3]])
        self.assertEqual([(t["volume"], t["pan"]) for t in out["tracks"]], [(100, 64), (101, 64)])

    def test_initial_predictor_scale(self):
        out = parse(brstm(channels=3))
        self.assertEqual(out["initial_predictor_scale"], ["0x23", "0x24", "0x25"])

    def test_truncation(self):
        blob = brstm(codec=1, channels=2, total=40000)
        self.assertFalse(parse(blob)["truncated"])
        self.assertTrue(parse(blob[:-100])["truncated"])
