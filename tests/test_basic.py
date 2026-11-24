import struct
import unittest

from adxkit.adx import AdxError, parse

LOOP_OFFSET = {3: 0x18, 4: 0x24, 5: 0x30}


def build(version=3, flags=0, block=18, bits=4, channels=2, rate=44100,
          total=32000, loop=None, frames=10, eos=True):
    copy_off = 0x60
    head = bytearray(copy_off + 4)
    head[0:2] = b"\x80\x00"
    struct.pack_into(">HBBBBIIHBB", head, 2, copy_off, 3, block, bits,
                     channels, rate, total, 500, version, flags)
    head[copy_off - 2:copy_off + 4] = b"(c)CRI"
    if loop:
        struct.pack_into(">IIIII", head, LOOP_OFFSET[version], *loop)
    body = bytearray()
    for _ in range(frames):
        body += struct.pack(">H", 0x0123) + bytes(block * channels - 2)
    if eos:
        body += b"\x80\x01" + bytes(block * channels - 2)
    return bytes(head + body)


class BasicTests(unittest.TestCase):
    def test_magic(self):
        with self.assertRaises(AdxError):
            parse(b"RIFF" + bytes(60))

    def test_fields(self):
        head = bytearray(0x40)
        head[0:2] = b"\x80\x00"
        head[2:4] = (0x3C).to_bytes(2, "big")
        head[4:8] = bytes([3, 18, 4, 1])
        head[0x3A:0x40] = b"(c)CRI"
        out = parse(bytes(head))
        self.assertEqual((out["encoding"], out["block_size"], out["channels"]), ("adx_standard", 18, 1))

    def test_rate_and_duration(self):
        out = parse(build(rate=48000, total=96000))
        self.assertEqual(out["sample_rate"], 48000)
        self.assertEqual(out["total_samples"], 96000)
        self.assertEqual(out["duration"], "2")

    def test_copyright_and_data_offset(self):
        out = parse(build())
        self.assertTrue(out["copyright_ok"])
        self.assertEqual(out["data_offset"], 0x60 + 4)

    def test_loop_versions(self):
        for version in (3, 4, 5):
            out = parse(build(version=version, loop=(1, 8000, 0x400, 31000, 0x1000)))
            self.assertTrue(out["loop"])
            self.assertEqual((out["loop_start"], out["loop_end"]), (8000, 31000))
            self.assertEqual((out["loop_start_byte"], out["loop_end_byte"]), (0x400, 0x1000))

    def test_loop_disabled(self):
        out = parse(build(version=4, loop=(0, 5, 6, 7, 8)))
        self.assertFalse(out["loop"])
        self.assertIsNone(out["loop_start"])
        self.assertIsNone(out["loop_end"])

    def test_encrypted(self):
        self.assertTrue(parse(build(flags=0x08))["encrypted"])
        self.assertFalse(parse(build(flags=0x00))["encrypted"])

    def test_samples_per_block(self):
        out = parse(build(block=32, frames=7, eos=False))
        self.assertEqual(out["samples_per_block"], 60)
        self.assertEqual(out["data_frames"], 7)
        self.assertEqual(out["data_samples"], 420)

    def test_end_of_stream_frame(self):
        out = parse(build(frames=4, eos=True))
        self.assertEqual(out["data_frames"], 4)
        self.assertEqual(out["data_samples"], 4 * 32)
