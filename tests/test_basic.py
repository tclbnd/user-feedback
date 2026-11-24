import struct
import unittest

from sphkit.sph import SphereError, parse

from files import tiny


def build(lines, size=1024, body=b""):
    """A SPHERE file whose header occupies exactly `size` bytes."""
    text = "NIST_1A\n%8d\n" % size + "".join(line + "\n" for line in lines) + "end_head\n"
    return text.encode().ljust(size, b" ") + body


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(tiny())
        self.assertEqual((out["header_bytes"], out["sample_rate"], out["sample_count"]), (1024, 8000, 2))

    def test_data(self):
        out = parse(tiny())
        self.assertEqual((out["data_bytes"], out["complete"]), (4, True))

    def test_string_fields_keep_spaces(self):
        out = parse(build([
            "sample_coding -s3 pcm",
            "prompt -s31 She had your dark suit in grea",
            "recording_date -s11 03-Jan-1987",
            "signal_to_noise -r 21.5",
        ]))
        self.assertEqual(out["fields"]["prompt"], "She had your dark suit in grea")
        self.assertEqual(out["fields"]["recording_date"], "03-Jan-1987")
        self.assertEqual(out["fields"]["signal_to_noise"], "21.5")

    def test_header_size_not_1024(self):
        body = struct.pack("<2h", 7, -9)
        out = parse(build(["sample_count -i 2", "sample_coding -s3 pcm"], size=2048, body=body))
        self.assertEqual((out["header_bytes"], out["data_bytes"]), (2048, 4))
        self.assertEqual(out["fields"]["sample_count"], 2)
        self.assertEqual(out["checksum_ok"], None)

    def test_big_endian_peak_and_checksum(self):
        samples = [1000, -2000, -32768]
        body = struct.pack(">3h", *samples)
        out = parse(build([
            "sample_count -i 3", "sample_rate -i 8000", "sample_n_bytes -i 2",
            "sample_byte_format -s2 10", "sample_coding -s3 pcm",
            "sample_checksum -i %d" % (sum(s & 0xFFFF for s in samples) % 65536),
        ], body=body))
        self.assertEqual(out["byte_order"], "big")
        self.assertEqual(out["peak"], "1.000000")
        self.assertTrue(out["checksum_ok"])

    def test_little_endian_checksum(self):
        samples = [1, -1, 256]
        body = struct.pack("<3h", *samples)
        out = parse(build([
            "sample_count -i 3", "sample_n_bytes -i 2", "sample_byte_format -s2 01",
            "sample_coding -s3 pcm", "sample_checksum -i %d" % (sum(s & 0xFFFF for s in samples) % 65536),
        ], body=body))
        self.assertTrue(out["checksum_ok"])
        self.assertEqual(out["peak"], "0.007812")

    def test_stereo_duration_is_per_channel(self):
        body = struct.pack("<8h", *([100, -100] * 4))
        out = parse(build([
            "sample_count -i 4", "sample_rate -i 8000", "channel_count -i 2",
            "sample_coding -s3 pcm",
        ], body=body))
        self.assertEqual((out["sample_count"], out["duration"]), (4, "1/2000"))
        self.assertEqual(out["data_bytes"], 16)
        self.assertTrue(out["complete"])

    def test_stereo_missing_sample_count(self):
        body = struct.pack("<8h", *([1] * 8))
        out = parse(build([
            "sample_rate -i 8000", "channel_count -i 2", "sample_coding -s3 pcm",
        ], body=body))
        self.assertEqual(out["sample_count"], 4)

    def test_ulaw_peak_is_decoded(self):
        # 0x80 is the largest negative µ-law code (-32124), far above what the
        # raw byte value (128) would suggest.
        out = parse(build([
            "sample_count -i 2", "sample_rate -i 8000", "sample_n_bytes -i 1",
            "sample_coding -s4 ulaw",
        ], body=bytes([0x80, 0x00])))
        self.assertEqual(out["coding"], "ulaw")
        self.assertEqual(out["duration"], "1/4000")
        self.assertEqual(out["peak"], "0.980347")

    def test_alaw_peak_is_decoded(self):
        # 0x2A is the largest negative A-law code (-32256).
        out = parse(build([
            "sample_count -i 2", "sample_n_bytes -i 1", "sample_coding -s4 alaw",
        ], body=bytes([0x2A, 0xAA])))
        self.assertEqual(out["peak"], "0.984375")

    def test_compressed(self):
        out = parse(build(["sample_count -i 3", "sample_coding -s20 pcm,embedded-shorten"]))
        self.assertTrue(out["compressed"])
        self.assertIsNone(out["complete"])
        self.assertIsNone(out["peak"])

    def test_bad_magic(self):
        with self.assertRaises(SphereError):
            parse(b"NOTSPHERE" + b" " * 2000)

