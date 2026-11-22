import unittest

from dpxkit.dpx import parse

from frames import dpx


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(dpx(width=100, height=20, creator="Film Lab"))
        self.assertEqual((out["endian"], out["version"], out["creator"], out["orientation"]), ("big", "V2.0", "Film Lab", 0))

    def test_rgb(self):
        el = parse(dpx(width=100, height=20))["elements"][0]
        self.assertEqual((el["descriptor"], el["bit_depth"], el["packing"]), ("RGB", 10, "filled_a"))
