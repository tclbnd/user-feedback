import struct
import unittest

from swavkit.swav import SwavError, parse


def pcm16_file(samples):
    payload = b"".join(struct.pack("<h", s) for s in samples)
    info = struct.pack("<BBHHHI", 1, 0, 22050, 759, 0, len(payload))
    data = b"DATA" + struct.pack("<I", 8 + len(info) + len(payload)) + info + payload
    return b"SWAV" + struct.pack("<HHIHH", 0xFEFF, 0x0100, 16 + len(data), 16, 1) + data


class BasicTests(unittest.TestCase):
    def test_pcm16_header(self):
        out = parse(pcm16_file([]))
        self.assertEqual(out["type"], "pcm16")
        self.assertEqual(out["sample_rate"], 22050)

    def test_not_swav(self):
        with self.assertRaises(SwavError):
            parse(b"STRM" + bytes(40))
