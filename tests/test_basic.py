import unittest

from sdskit.sds import parse

HEADER = bytes([0xF0, 0x7E, 0x00, 0x01, 0x00, 0x00, 8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x7F, 0xF7])


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(HEADER)
        self.assertEqual((out["device_id"], out["bits"], out["packets"]), (0, 8, 0))

    def test_no_samples(self):
        out = parse(HEADER)
        self.assertEqual((out["samples"], out["first_sample"]), (0, None))
