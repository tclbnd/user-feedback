import unittest

from mackit.mac import parse

from headers import new, old


class BasicTests(unittest.TestCase):
    def test_format(self):
        out = parse(new(version=3990, level=3000, ch=2, rate=48000))
        self.assertEqual((out["version"], out["compression"], out["channels"], out["sample_rate"]), ("3.99", "high", 2, 48000))

    def test_frames(self):
        out = parse(new(frames=7, bpf=73728 * 4))
        self.assertEqual((out["total_frames"], out["blocks_per_frame"], out["seek_entries"]), (7, 294912, 7))


class OldLayoutTests(unittest.TestCase):
    def test_header_and_peak(self):
        out = parse(old(version=3800, level=4000, peak=123456))
        self.assertEqual((out["layout"], out["version"], out["compression"]), ("old", "3.80", "extra high"))
        self.assertEqual((out["peak_level"], out["md5"], out["frame_data_bytes"]), (123456, None, None))

    def test_blocks_per_frame_by_version(self):
        cases = [(3500, 2000, 9216), (3800, 2000, 9216), (3800, 4000, 73728),
                 (3900, 2000, 73728), (3950, 2000, 294912), (3970, 5000, 294912)]
        for version, level, bpf in cases:
            out = parse(old(version=version, level=level))
            self.assertEqual(out["blocks_per_frame"], bpf, (version, level))

    def test_total_blocks_includes_final_frame(self):
        out = parse(old(version=3950, frames=7, final=333))
        self.assertEqual(out["total_blocks"], 6 * 294912 + 333)


class DescriptorTests(unittest.TestCase):
    def test_total_blocks_includes_final_frame(self):
        out = parse(new(frames=7, bpf=1000, final=333))
        self.assertEqual((out["total_blocks"], out["duration"]), (6 * 1000 + 333, "2111/14700"))

    def test_24bit_from_header(self):
        self.assertEqual(parse(new(bits=24))["bits"], 24)

    def test_frame_bytes_64bit(self):
        self.assertEqual(parse(new(frame_bytes=5_000_000_000))["frame_data_bytes"], 5_000_000_000)

    def test_id3v2_skipped(self):
        tag = b"ID3\x03\x00\x00" + bytes([0, 0, 0, 50]) + bytes(50)
        out = parse(new(id3=tag))
        self.assertEqual((out["id3v2_bytes"], out["layout"], out["first_frame_offset"]), (60, "descriptor", 60))

    def test_id3v2v4_footer(self):
        tag = b"ID3\x04\x00\x10" + bytes([0, 0, 0, 10]) + bytes(10) + b"3DI\x04\x00\x10" + bytes([0, 0, 0, 10])
        self.assertEqual(parse(new(id3=tag))["id3v2_bytes"], 30)
