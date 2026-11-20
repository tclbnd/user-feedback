import struct
import unittest

from flvscan import amf, segments


class BasicTests(unittest.TestCase):
    def test_amf_object(self):
        b = b"\x03" + b"\x00\x05width" + b"\x00" + struct.pack(">d", 1280) + b"\x00\x00\x09"
        self.assertEqual(amf.value(b, 0)[0], {"width": 1280.0})

    def test_segments(self):
        self.assertEqual(segments.segment_starts([0, 30000, 61000, 90000, 125000]), [0, 61000, 125000])
        self.assertEqual(segments.hms(3723004), "1:02:03.004")


if __name__ == "__main__":
    unittest.main()
