import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build import cluster, frame_bytes, mkv, simple, track  # noqa: E402
from mkvkit.ebml import EbmlError  # noqa: E402
from mkvkit.mkv import scan  # noqa: E402


class BasicTest(unittest.TestCase):
    def test_two_tracks(self):
        data = mkv([track(1, 1, "V_VP9"), track(2, 2, "A_OPUS")],
                   [cluster(0, [simple(1, 0, [frame_bytes(500)]), simple(2, 0, [frame_bytes(100), frame_bytes(120)], mode="xiph")]),
                    cluster(40, [simple(1, 0, [frame_bytes(300)], key=False)])], duration=80.0)
        out = scan(data)
        v, a = out["tracks"]
        self.assertEqual((v["codec"], v["blocks"], v["keyframes"], v["bytes"]), ("V_VP9", 2, 1, 800))
        self.assertEqual((a["frames"], a["bytes"]), (2, 220))
        self.assertEqual(v["last_ms"], "40")
        self.assertEqual(out["clusters"], 2)

    def test_not_ebml(self):
        with self.assertRaises(EbmlError):
            scan(b"RIFF" + bytes(20))


if __name__ == "__main__":
    unittest.main()
