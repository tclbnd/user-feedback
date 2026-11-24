import unittest

from avrkit.avr import parse

HEAD = b"2BIT" + b"KICK".ljust(8, b"\0") + bytes(116)


class BasicTests(unittest.TestCase):
    def test_name(self):
        out = parse(HEAD)
        self.assertEqual((out["name"], out["frames"]), ("KICK", 0))

    def test_not_avr(self):
        with self.assertRaises(ValueError):
            parse(b"RIFF" + bytes(200))
