import unittest

from zx81kit.dfile import DisplayError, parse


class BasicTests(unittest.TestCase):
    def test_digits_on_full_screen(self):
        rows = bytes([0x76]) + (bytes([28, 29, 30]) + bytes(29) + b"\x76") * 24
        out = parse(b"ZX81DF" + bytes(512) + rows)
        self.assertEqual(out["text"][0], "012")
        self.assertEqual(out["rows"], 24)

    def test_not_a_dump(self):
        with self.assertRaises(DisplayError):
            parse(b"ZX81XX" + bytes(600))
