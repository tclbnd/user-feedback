import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from clips import Track, mp4  # noqa: E402
from mp4kit.boxes import Mp4Error  # noqa: E402
from mp4kit.tracks import movie  # noqa: E402


class BasicTest(unittest.TestCase):
    def test_single_chunk(self):
        out = movie(mp4([Track(sizes=[400] * 8, chunking=[8], sync=[1])]))
        t = out["tracks"][0]
        self.assertEqual(out["timescale"], 1000)
        self.assertEqual(t["handler"], "vide")
        self.assertEqual(t["samples"], 8)
        self.assertEqual(t["bytes"], 3200)
        self.assertEqual(t["last_offset"] - t["first_offset"], 2800)
        self.assertEqual(t["media_duration"], "4/15")

    def test_two_tracks(self):
        out = movie(mp4([Track(sizes=[400] * 6, chunking=[3, 3], sync=[1, 4]),
                         Track(tid=2, handler="soun", scale=44100, sizes=[200] * 4, deltas=[1024] * 4, chunking=[4], sync=[1])]))
        self.assertEqual([t["id"] for t in out["tracks"]], [1, 2])
        self.assertEqual(out["tracks"][0]["sync_samples"], 2)
        self.assertEqual(out["tracks"][1]["chunks"], 1)

    def test_no_moov(self):
        with self.assertRaises(Mp4Error):
            movie(b"\x00\x00\x00\x08free")


if __name__ == "__main__":
    unittest.main()
