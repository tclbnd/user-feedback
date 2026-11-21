import unittest

from psscan.scan import scan

from streams import END, pack2, pes2, system_header


class BasicTests(unittest.TestCase):
    def test_mpeg2_video(self):
        data = pack2(90000 * 2) + system_header() + pes2(0xE0, b"\x00" * 64, pts=180000) + pes2(0xC0, b"\x11" * 32, pts=180000) + END
        out = scan(data)
        self.assertEqual((out["format"], out["packs"]), ("MPEG-2", 1))
        self.assertEqual(out["streams"]["0xe0"]["payload_bytes"], 64)
        self.assertEqual(out["streams"]["0xc0"]["first_pts"], "2")


if __name__ == "__main__":
    unittest.main()
