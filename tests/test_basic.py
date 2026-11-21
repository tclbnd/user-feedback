import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build import adtl, cue, wav  # noqa: E402
from wavkit.riff import WavError, parse  # noqa: E402


class BasicTest(unittest.TestCase):
    def test_markers(self):
        out = parse(wav(cue([(1, 1000), (2, 5000)]), adtl([(1, "Intro"), (2, "Verse")])))
        self.assertEqual(out["frames"], 48000)
        self.assertEqual([(m["id"], m["sample"], m["label"]) for m in out["markers"]], [(1, 1000, "Intro"), (2, 5000, "Verse")])
        self.assertEqual(out["markers"][1]["time"], "5/48")

    def test_plain(self):
        out = parse(wav(frames=100, ch=1, bits=16, rate=44100))
        self.assertEqual((out["channels"], out["bits"], out["frames"], out["markers"]), (1, 16, 100, []))

    def test_not_wav(self):
        with self.assertRaises(WavError):
            parse(b"RIFF\x04\x00\x00\x00AVI ")


if __name__ == "__main__":
    unittest.main()
