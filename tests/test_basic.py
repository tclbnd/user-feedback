import unittest

from cdtextkit.cdtext import parse

PACK = bytes([0x80, 1, 0, 0]) + b"Song\0".ljust(12, b"\0") + b"\0\0"


class BasicTests(unittest.TestCase):
    def test_count(self):
        self.assertEqual(parse(PACK * 2)["packs"], 2)

    def test_block(self):
        self.assertEqual(parse(PACK)["blocks"][0]["block"], 0)
