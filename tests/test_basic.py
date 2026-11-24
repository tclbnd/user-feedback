import unittest

from gymkit.gym import parse

LOG = b"\x01\x22\x00\x02\xa4\x22\x00\x00"


class BasicTests(unittest.TestCase):
    def test_writes(self):
        out = parse(LOG)
        self.assertEqual((out["ym_port0_writes"], out["ym_port1_writes"], out["frames"]), (1, 1, 2))

    def test_no_header(self):
        self.assertIsNone(parse(LOG)["header"])
