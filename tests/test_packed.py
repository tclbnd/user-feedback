import struct
import unittest

from itkit.it import parse


def sample_header(name, flags, length, loop, sustain, c5, pointer):
    h = bytearray(80)
    h[0:4] = b"IMPS"
    h[0x11] = flags
    h[0x14:0x14 + len(name)] = name
    struct.pack_into("<7I", h, 0x30, length, loop[0], loop[1], c5,
                     sustain[0], sustain[1], pointer)
    return bytes(h)


def build_module():
    orders = bytes([0, 254, 1, 255, 0])
    # Packed pattern 0: three rows of events, then empty rows.
    pat0 = bytearray()
    pat0 += bytes([1])                       # row 0, channel 0
    pat0 += bytes([0x01, 60])                # full mask = note only; note 60
    pat0 += bytes([0])                       # end row
    pat0 += bytes([1])                       # row 1, channel 0
    pat0 += bytes([0x44, 61, 40])            # partial mask: add volpan; note 61
    pat0 += bytes([130, 0x05, 254, 40])      # channel 1; mask note+volpan; note off
    pat0 += bytes([0])                       # end row
    pat0 += bytes([3])                       # row 2, channel 2
    pat0 += bytes([0x01, 253])               # full mask = note only; note cut
    pat0 += bytes([0])                       # end row
    pat0_block = struct.pack("<HH", len(pat0), 8) + bytes(4) + bytes(pat0)

    message = b"hello\rworld\0"
    sample_data = bytes(range(16))           # 16 bytes of sample data

    p = 192 + len(orders) + 8 + 8
    smp0_off = p
    smp1_off = p + 80
    data_off = smp1_off + 80
    pat0_off = data_off + len(sample_data)
    msg_off = pat0_off + len(pat0_block)

    h = bytearray(192)
    h[0:4] = b"IMPM"
    h[4:9] = b"Songs"
    struct.pack_into("<8H", h, 32, len(orders), 0, 2, 2, 0x0214, 0x0200,
                     0x0D, 0x01)
    h[48:53] = bytes([128, 48, 6, 125, 128])
    struct.pack_into("<HI", h, 54, len(message), msg_off)
    # channels: off, surround, muted surround, muted, normal -> 3 active
    h[64:70] = bytes([0x00, 0x40, 0xC0, 0x80, 0x30, 0x10])

    # Body: orders, sample offsets, pattern offsets.
    body = orders
    body += struct.pack("<II", smp0_off, smp1_off)
    body += struct.pack("<II", pat0_off, 0)

    out = bytes(h) + body
    smp0 = sample_header(b"loud", 0x3F, 4, (1, 3), (0, 2), 8000, data_off)
    smp1 = sample_header(b"squashed", 0x40, 10, (0, 0), (0, 0), 8363, 0)
    return out + smp0 + smp1 + sample_data + pat0_block + message


class PackedTests(unittest.TestCase):
    def setUp(self):
        self.out = parse(build_module())

    def test_versions_and_orders(self):
        self.assertEqual((self.out["tracker"], self.out["compatible"]),
                         ("2.14", "2.00"))
        self.assertEqual(self.out["orders"], [0, 1])

    def test_channels(self):
        self.assertEqual(self.out["channels"], 3)
        self.assertEqual(self.out["surround_channels"], 1)

    def test_samples(self):
        a, b = self.out["samples"]
        self.assertEqual(a["name"], "loud")
        self.assertEqual(a["length"], 4)
        self.assertEqual(a["bits"], 16)
        self.assertTrue(a["stereo"])
        self.assertEqual(a["c5_speed"], 8000)
        self.assertEqual(a["loop"], "pingpong")
        self.assertEqual(a["sustain_loop"], "pingpong")
        self.assertFalse(a["compressed"])
        self.assertEqual(a["data_bytes"], 16)
        self.assertEqual(b["name"], "squashed")
        self.assertTrue(b["compressed"])
        self.assertIsNone(b["data_bytes"])

    def test_forward_loops(self):
        # flags 0x05: normal loop + sustain loop, both forward, 8-bit mono
        h = bytearray(192)
        h[0:4] = b"IMPM"
        struct.pack_into("<8H", h, 32, 0, 0, 1, 0, 0x0214, 0x0200, 0, 0)
        smp = sample_header(b"hum", 0x05, 100, (10, 90), (20, 80), 8363, 192 + 4 + 80)
        body = struct.pack("<I", 192 + 4)
        out = parse(bytes(h) + body + smp + bytes(100))
        got = out["samples"][0]
        self.assertEqual(got["bits"], 8)
        self.assertFalse(got["stereo"])
        self.assertEqual(got["loop"], "forward")
        self.assertEqual(got["sustain_loop"], "forward")
        self.assertEqual(got["data_bytes"], 100)

    def test_patterns_and_events(self):
        self.assertEqual(self.out["pattern_rows"], [8, 64])
        self.assertEqual(self.out["notes"], [2, 0])
        self.assertEqual(self.out["note_offs"], [1, 0])
        self.assertEqual(self.out["note_cuts"], [1, 0])

    def test_message(self):
        self.assertEqual(self.out["message"], "hello\nworld")


if __name__ == "__main__":
    unittest.main()
