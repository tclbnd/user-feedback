import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from flackit.flac import FlacError, scan  # noqa: E402
from streams import block, flac, frame, streaminfo, vorbis  # noqa: E402


class BasicTest(unittest.TestCase):
    def test_fixed_blocking(self):
        out = scan(flac([frame(i, seed=i) for i in range(6)], streaminfo(total=6 * 4096)))
        self.assertEqual(out["sample_rate"], 44100)
        self.assertEqual(out["total_samples"], 24576)
        self.assertEqual(out["frames"], 6)
        self.assertEqual(out["last_number"], 5)
        self.assertEqual(out["blocking"], "fixed")

    def test_vorbis_comment(self):
        out = scan(flac([frame(0)], streaminfo(), [block(4, vorbis(tags=["ARTIST=Mira"]))]))
        self.assertEqual(out["tags"], ["ARTIST=Mira"])
        self.assertEqual([b["type"] for b in out["metadata"]], ["STREAMINFO", "VORBIS_COMMENT"])

    def test_not_flac(self):
        with self.assertRaises(FlacError):
            scan(b"OggS" + bytes(60))


if __name__ == "__main__":
    unittest.main()
