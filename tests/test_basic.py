import unittest

from vagkit.vag import VagError, parse


class BasicTests(unittest.TestCase):
    def test_silent_frame(self):
        data = b"VAGp" + bytes(28) + b"beep".ljust(16, b"\0") + bytes(16)
        out = parse(data)
        self.assertEqual(out["name"], "beep")
        self.assertEqual(out["peak"], "0.000000")

    def test_not_vag(self):
        with self.assertRaises(VagError):
            parse(b"RIFF" + bytes(60))
