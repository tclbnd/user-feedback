import struct
import unittest

from rstmkit.rstm import parse

from streams import brstm


def stream_info_offset(data):
    """Offset of the stream info section inside a big-endian file."""
    head = 0x40 + 8
    return head + struct.unpack_from(">I", data, head + 4)[0]


class BasicTests(unittest.TestCase):
    def test_layout(self):
        out = parse(brstm(channels=2, total=50000))
        self.assertEqual((out["codec"], out["channels"], out["total_samples"], out["blocks"]), ("dsp-adpcm", 2, 50000, 4))

    def test_adpc(self):
        out = parse(brstm(channels=1, total=20000, tracks=[[0]]))
        self.assertEqual((out["has_adpc"], out["channels"], out["version"]), (True, 1, "1.0"))

    def test_sample_rate_is_u16(self):
        # The u16 sample rate is followed by a padding u16, not by more rate bits.
        out = parse(brstm(channels=2, rate=32000, total=50000))
        self.assertEqual((out["sample_rate"], out["duration"]), (32000, "25/16"))

    def test_loop_uses_flag_not_start(self):
        # A stream whose loop start is stale but whose loop flag is clear must
        # not be reported as looping.
        self.assertIsNone(parse(brstm(channels=1, total=4410, stale_loop=2205))["loop"])
        self.assertEqual(parse(brstm(channels=1, total=4410, loop=0))["loop"]["start"], 0)

    def test_duration_uses_total_samples(self):
        out = parse(brstm(channels=1, total=4410, rate=22050))
        self.assertEqual((out["total_samples"], out["duration"]), (4410, "1/5"))

    def test_codec_ids(self):
        for codec, name in ((0, "pcm8"), (1, "pcm16"), (2, "dsp-adpcm")):
            self.assertEqual(parse(brstm(codec=codec, channels=1, total=100))["codec"], name)

    def test_tracks_read_channel_lists(self):
        out = parse(brstm(codec=2, channels=4, total=100000))
        self.assertEqual([t["channels"] for t in out["tracks"]], [[0, 1], [2, 3]])
        self.assertEqual((out["tracks"][0]["volume"], out["tracks"][0]["pan"]), (100, 64))

    def test_tracks_without_volume_pan(self):
        out = parse(brstm(codec=0, channels=2, total=1000, tracks=[[0], [1]], track_type=0))
        self.assertEqual([t["channels"] for t in out["tracks"]], [[0], [1]])
        self.assertIsNone(out["tracks"][0]["volume"])

    def test_initial_predictor_scale(self):
        out = parse(brstm(codec=2, channels=3, total=1000))
        self.assertEqual(out["initial_predictor_scale"], ["0x23", "0x24", "0x25"])
        self.assertIsNone(parse(brstm(codec=1, channels=1, total=1000))["initial_predictor_scale"])

    def test_data_bytes_pad_last_block_to_32(self):
        data = brstm(codec=0, channels=1, total=14)
        out = parse(data)
        self.assertEqual(out["data_bytes_expected"], 32)
        self.assertFalse(out["truncated"])

    def test_truncated_detects_short_data(self):
        data = brstm(codec=1, channels=2, total=100000)
        self.assertFalse(parse(data)["truncated"])
        self.assertTrue(parse(data[:-32])["truncated"])

    def test_little_endian_file(self):
        # The builder can emit either byte order; a 0xFF 0xFE mark behind the
        # magic selects little-endian parsing.
        out = parse(brstm(codec=2, channels=2, rate=32000, total=50000, endian="<"))
        self.assertEqual((out["codec"], out["sample_rate"], out["duration"]),
                         ("dsp-adpcm", 32000, "25/16"))
        self.assertEqual(out["initial_predictor_scale"], ["0x23", "0x24"])

    def test_zeroed_block_count_derived_from_samples(self):
        data = bytearray(brstm(codec=1, channels=1, total=1000))
        struct.pack_into(">I", data, stream_info_offset(data) + 0x14, 0)
        self.assertGreater(parse(bytes(data))["data_bytes_expected"], 0)

