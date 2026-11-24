import unittest

from ttxkit.ttx import TtxError, parse


class BasicTests(unittest.TestCase):
    def test_packets(self):
        self.assertEqual(parse(bytes(42 * 3))["packets"], 3)

    def test_size(self):
        with self.assertRaises(TtxError):
            parse(bytes(50))
