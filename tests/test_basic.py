import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from latmkit.latm import LatmError, scan  # noqa: E402
from streams import asc, smc, stream  # noqa: E402


class BasicTest(unittest.TestCase):
    def test_aac_lc_stereo(self):
        out = scan(stream(smc(asc(rate=48000, chconf=2)), count=4, size=100))
        self.assertEqual(out["object_type"], 2)
        self.assertEqual(out["sample_rate"], 48000)
        self.assertEqual(out["channels"], 2)
        self.assertEqual(out["access_units"], 4)
        self.assertEqual(out["payload_bytes"], 400)
        self.assertEqual(out["duration"], "32/375")

    def test_same_stream_mux(self):
        out = scan(stream(smc(asc(rate=44100, chconf=1)), count=6, size=50))
        self.assertEqual(out["configs"], 1)
        self.assertEqual(out["loas_frames"], 6)

    def test_no_config(self):
        with self.assertRaises(LatmError):
            scan(b"\x00" * 16)


if __name__ == "__main__":
    unittest.main()
