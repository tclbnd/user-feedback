import unittest

from apekit.mac import parse

from headers import FLAG_NO_WAV, FLAG_PEAK, id3v2, new, old


class BasicTests(unittest.TestCase):
    def test_format(self):
        out = parse(new(version=3990, level=3000, ch=2, rate=48000))
        self.assertEqual((out["version"], out["compression"], out["channels"], out["sample_rate"]), ("3.99", "high", 2, 48000))

    def test_frames(self):
        out = parse(new(frames=7, bpf=73728 * 4))
        self.assertEqual((out["total_frames"], out["blocks_per_frame"], out["seek_entries"]), (7, 294912, 7))

    def test_total_blocks_counts_partial_final_frame(self):
        out = parse(new(frames=7, bpf=294912, final=12345))
        self.assertEqual(out["total_blocks"], 6 * 294912 + 12345)
        self.assertEqual(out["final_frame_blocks"], 12345)

    def test_duration_follows_total_blocks(self):
        out = parse(new(frames=3, bpf=1000, final=400, rate=44100))
        self.assertEqual(out["total_blocks"], 2400)
        self.assertEqual(out["duration"], "8/147")

    def test_descriptor_sample_size(self):
        """A 24-bit master must report 24 bits, not fall back to 16."""
        out = parse(new(bits=24))
        self.assertEqual(out["bits"], 24)

    def test_descriptor_stream_size_over_4gib(self):
        out = parse(new(frame_bytes=6_442_450_944))       # 6 GiB
        self.assertEqual(out["frame_data_bytes"], 6_442_450_944)

    def test_descriptor_stream_size_under_4gib(self):
        out = parse(new(frame_bytes=3_000_000_000))
        self.assertEqual(out["frame_data_bytes"], 3_000_000_000)

    def test_id3v2_in_front(self):
        tag = id3v2(b"\x00" * 512)
        out = parse(new(id3=tag))
        self.assertEqual((out["id3v2_bytes"], out["layout"], out["version"]), (len(tag), "descriptor", "3.99"))

    def test_id3v2_v23(self):
        tag = id3v2(b"\x00" * 300, version=3)
        out = parse(new(id3=tag))
        self.assertEqual(out["id3v2_bytes"], 310)

    def test_id3v2_with_footer(self):
        tag = id3v2(b"\x00" * 100, footer=True)
        out = parse(new(id3=tag))
        self.assertEqual(out["id3v2_bytes"], len(tag))

    def test_old_layout_from_1999(self):
        out = parse(old(version=3800, level=4000, bits=16, frames=37, final=30111))
        self.assertEqual(out["layout"], "old")
        self.assertEqual((out["version"], out["compression"], out["bits"]), ("3.80", "extra high", 16))
        self.assertEqual(out["blocks_per_frame"], 73728)   # 3.80 extra high
        self.assertEqual(out["total_blocks"], 36 * 73728 + 30111)
        self.assertIsNone(out["frame_data_bytes"])
        self.assertIsNone(out["md5"])

    def test_old_blocks_per_frame_by_version(self):
        """Old encoders changed the frame size; the version decides the layout."""
        cases = [(3100, 2000, 9216), (3500, 3000, 9216), (3800, 2000, 9216),
                 (3800, 4000, 73728), (3900, 2000, 73728), (3940, 3000, 73728), (3950, 1000, 73728 * 4)]
        for version, level, bpf in cases:
            with self.subTest(version=version, level=level):
                out = parse(old(version=version, level=level))
                self.assertEqual(out["blocks_per_frame"], bpf)

    def test_old_seek_entries_from_flag(self):
        out = parse(old(seek=11))
        self.assertEqual(out["seek_entries"], 11)

    def test_old_seek_entries_one_per_frame_when_flag_clear(self):
        out = parse(old(frames=9, seek=None))
        self.assertEqual(out["seek_entries"], 9)

    def test_old_peak_level(self):
        self.assertEqual(parse(old(flags=FLAG_PEAK, peak=31873))["peak_level"], 31873)
        self.assertIsNone(parse(old(flags=0))["peak_level"])

    def test_old_seek_read_after_peak(self):
        """The seek count sits after the optional peak field, not at a fixed offset."""
        out = parse(old(flags=FLAG_PEAK, peak=999, seek=5))
        self.assertEqual((out["peak_level"], out["seek_entries"]), (999, 5))

    def test_old_sample_sizes(self):
        self.assertEqual(parse(old(bits=8))["bits"], 8)
        self.assertEqual(parse(old(bits=24))["bits"], 24)
        self.assertEqual(parse(old(flags=0))["bits"], 16)

    def test_old_with_id3v2(self):
        tag = id3v2(b"\x00" * 64)
        data = tag + old(version=3800, bits=16)
        out = parse(data)
        self.assertEqual((out["id3v2_bytes"], out["layout"], out["version"]), (len(tag), "old", "3.80"))

    def test_multiple_id3v2_tags(self):
        tag = id3v2(b"\x00" * 16)
        out = parse(new(id3=tag + tag))
        self.assertEqual(out["id3v2_bytes"], 2 * len(tag))

    def test_no_stored_wav_header(self):
        self.assertEqual(parse(new(flags=FLAG_NO_WAV, wav=44))["wav_header_bytes"], 0)
        self.assertEqual(parse(old(flags=FLAG_NO_WAV, wav=44))["wav_header_bytes"], 0)

    def test_stored_wav_header(self):
        self.assertEqual(parse(new(wav=44))["wav_header_bytes"], 44)
        self.assertEqual(parse(old(flags=0, wav=44))["wav_header_bytes"], 44)
