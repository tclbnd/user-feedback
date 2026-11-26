import unittest

from pcvid.video import VideoError, parse


class BasicTests(unittest.TestCase):
    def test_blank_cga(self):
        data = b"PCVID" + bytes([4, 0]) + bytes(16) + bytes(0x4000)
        out = parse(data)
        self.assertEqual(out["colors"], {"#000000": 320 * 200})

    def test_not_a_dump(self):
        with self.assertRaises(VideoError):
            parse(b"PCXXX" + bytes(30))
