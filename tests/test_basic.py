import unittest

from vtfkit.vtf import VtfError, parse
from tests.textures import plain


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(plain())
        self.assertEqual((out["version"], out["width"], out["height"], out["format"]), ("7.2", 32, 16, "bgr888"))

    def test_not_vtf(self):
        with self.assertRaises(VtfError):
            parse(b"VTX\0" + bytes(100))
