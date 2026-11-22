"""End-to-end checks against the sample files shipped in examples/."""
import os
import unittest

from apekit.mac import parse

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")


def load(name):
    with open(os.path.join(EXAMPLES, name), "rb") as fh:
        return parse(fh.read())


class ExampleTests(unittest.TestCase):
    def test_remaster_header(self):
        out = load("remaster.ape")
        self.assertEqual(out["id3v2_bytes"], 522)          # ID3v2 tag in front of the stream
        self.assertEqual((out["version"], out["layout"]), ("3.99", "descriptor"))
        self.assertEqual((out["channels"], out["bits"], out["sample_rate"]), (2, 24, 96000))
        self.assertEqual((out["compression"], out["blocks_per_frame"]), ("extra high", 294912))
        self.assertEqual((out["total_frames"], out["final_frame_blocks"]), (21, 201311))
        self.assertEqual(out["seek_entries"], 21)
        self.assertEqual(out["frame_data_bytes"], 4_600_000_000)   # beyond 4 GiB
        self.assertEqual(out["md5"], "000102030405060708090a0b0c0d0e0f")

    def test_remaster_length(self):
        out = load("remaster.ape")
        self.assertEqual(out["total_blocks"], 20 * 294912 + 201311)
        self.assertEqual(out["duration"], "6099551/96000")

    def test_1999_rip_header(self):
        out = load("1999_rip.ape")
        self.assertEqual((out["version"], out["layout"]), ("3.80", "old"))
        self.assertEqual((out["channels"], out["bits"], out["sample_rate"]), (2, 16, 44100))
        self.assertEqual((out["compression"], out["blocks_per_frame"]), ("extra high", 73728))
        self.assertEqual((out["total_frames"], out["final_frame_blocks"]), (37, 30111))
        self.assertEqual(out["seek_entries"], 37)
        self.assertEqual(out["peak_level"], 31873)
        self.assertIsNone(out["frame_data_bytes"])
        self.assertIsNone(out["md5"])

    def test_1999_rip_length(self):
        out = load("1999_rip.ape")
        self.assertEqual(out["total_blocks"], 36 * 73728 + 30111)
        self.assertEqual(out["duration"], "894773/14700")
