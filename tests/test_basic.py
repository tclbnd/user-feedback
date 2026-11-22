import unittest

from mxfkit.mxf import parse

from files import header_metadata, index_segment, mxf


class BasicTests(unittest.TestCase):
    def test_partitions(self):
        out = parse(mxf(header_metadata(25, 100), frames=3))
        self.assertEqual([p["kind"] for p in out["partitions"]], ["header", "footer"])
        self.assertEqual((out["operational_pattern"], out["rip"]), ("OP1a", True))

    def test_video(self):
        v = parse(mxf(header_metadata(25, 100, (1280, 720, 0, 8)), frames=2))["video"][0]
        self.assertEqual((v["width"], v["height"], v["bit_depth"]), (1280, 720, 8))
