import unittest

from sphkit.sph import parse

from files import tiny


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(tiny())
        self.assertEqual((out["header_bytes"], out["sample_rate"], out["sample_count"]), (1024, 8000, 2))

    def test_data(self):
        out = parse(tiny())
        self.assertEqual((out["data_bytes"], out["complete"]), (4, True))
