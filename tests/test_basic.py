import unittest

from stlkit.stl import parse


def gsi(title=b"NEWS"):
    g = bytearray(b" " * 1024)
    g[0:14] = b"850STL25.01100"
    g[16:16 + len(title)] = title
    g[256:264] = b"10000000"
    return bytes(g)


def tti(sn, text):
    return bytes([0]) + sn.to_bytes(2, "little") + bytes([0xFF, 0]) + bytes([10, 0, 0, 0, 10, 0, 2, 0, 20, 2, 0]) + text.ljust(112, b"\x8f")


class BasicTests(unittest.TestCase):
    def test_gsi(self):
        out = parse(gsi() + tti(1, b"Hello"))
        self.assertEqual((out["programme"], out["start_timecode"], out["tti_blocks"]), ("NEWS", "10:00:00:00", 1))

    def test_subtitle(self):
        sub = parse(gsi() + tti(1, b"Hello"))["subtitles"][0]
        self.assertEqual((sub["number"], sub["in"], sub["lines"]), (1, "10:00:00:00", ["Hello"]))
