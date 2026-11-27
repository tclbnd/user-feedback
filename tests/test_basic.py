import unittest

from gxkit.galaxian import GxError, parse


def snapshot(vram=b"", obj=b"", rom=b""):
    return (b"GALAXSNP" + vram.ljust(1024, b"\0") + obj.ljust(256, b"\0") + rom.ljust(4096, b"\0"))


class BasicTests(unittest.TestCase):
    def test_blank_screen(self):
        out = parse(snapshot())
        self.assertEqual(out["tiles_used"], 0)
        self.assertEqual(out["colours_used"], 1)

    def test_wrong_length(self):
        with self.assertRaises(GxError):
            parse(b"GALAXSNP" + bytes(100))


if __name__ == "__main__":
    unittest.main()
