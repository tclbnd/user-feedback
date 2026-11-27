import unittest

from cinkit.idcin import CinError, parse


class BasicTests(unittest.TestCase):
    def test_header_only(self):
        data = b"".join(v.to_bytes(4, "little") for v in (8, 4, 0, 0, 0)) + bytes(65536)
        out = parse(data)
        self.assertEqual(out["frames"], [])
        self.assertIsNone(out["audio"])

    def test_too_short(self):
        with self.assertRaises(CinError):
            parse(bytes(100))


if __name__ == "__main__":
    unittest.main()
