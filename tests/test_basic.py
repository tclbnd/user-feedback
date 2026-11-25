import unittest

from tzxkit.tzx import TzxError, parse


class BasicTests(unittest.TestCase):
    def test_empty_tape(self):
        out = parse(b"ZXTape!\x1a\x01\x14")
        self.assertEqual((out["version"], out["blocks"], out["duration"]), ("1.20", 0, "0"))

    def test_not_tzx(self):
        with self.assertRaises(TzxError):
            parse(b"ZXTape?\x1a\x01\x14")
