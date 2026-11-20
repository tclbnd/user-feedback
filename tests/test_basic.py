import unittest

from caftool.chunks import CafError, chunks
from caftool.info import summary

from files import caf, chan, data, desc, info, pakt, pcm


def build(*parts):
    return caf(*parts)


class HeaderTests(unittest.TestCase):
    def test_sample_rate_is_float64(self):
        out = summary(build(desc(44100.0), data(pcm([(0, 0)]))))
        self.assertEqual(out["sample_rate"], 44100.0)

    def test_fractional_sample_rate(self):
        out = summary(build(desc(44100.5), data(pcm([(0, 0)]))))
        self.assertEqual(out["sample_rate"], 44100.5)

    def test_not_a_caf(self):
        with self.assertRaises(CafError):
            chunks(b"RIFF....")


class DataChunkTests(unittest.TestCase):
    def test_edit_count_is_not_audio(self):
        # A 16-bit stereo file: 4 bytes per frame.  The 4-byte edit count used
        # to be counted as a whole extra frame.
        audio = pcm([(1, 2), (3, 4), (5, 6)])
        out = summary(build(desc(48000.0, bpp=4, fpp=1, ch=2, bits=16), data(audio)))
        self.assertEqual(out["packets"], 3)
        self.assertEqual(out["frames"], 3)
        self.assertEqual(out["duration"], round(3 / 48000.0, 6))

    def test_edit_count_skipped_for_peak(self):
        out = summary(build(desc(48000.0), data(pcm([(-32768, 32767)]), edit=12345)))
        self.assertEqual(out["peak"], [1.0, round(32767 / 32768, 6)])

    def test_streaming_data_length_minus_one(self):
        audio = pcm([(100, 200), (300, 400)])
        raw = build(desc(48000.0), data(audio, size=-1))
        out = summary(raw)
        self.assertEqual(out["frames"], 2)
        self.assertEqual(out["peak"], [round(300 / 32768, 6), round(400 / 32768, 6)])

    def test_truncated_chunk_still_reported(self):
        raw = caf(desc(48000.0), data(pcm([(1, 2)])))
        with self.assertRaises(CafError):
            chunks(raw[:-1])


class PcmTests(unittest.TestCase):
    def test_big_endian_is_default(self):
        # CAF LPCM flags of zero mean big-endian samples.
        out = summary(build(desc(48000.0, bpp=4, ch=2, bits=16),
                            data(pcm([(8192, -16384)]))))
        self.assertEqual(out["peak"], [0.25, 0.5])

    def test_little_endian_flag(self):
        little = 1 << 1
        out = summary(build(desc(48000.0, flags=little, bpp=4, ch=2, bits=16),
                            data(pcm([(8192, -16384)], little=True))))
        self.assertEqual(out["peak"], [0.25, 0.5])

    def test_eight_bit_signed(self):
        # CAF linear PCM integers are two's-complement signed, including 8-bit.
        audio = bytes([0x00, 0x80, 0xFF, 0x40])  # ch0: 0,-1; ch1: -128,64
        out = summary(build(desc(22050.0, bpp=2, ch=2, bits=8), data(audio)))
        self.assertEqual(out["peak"], [round(1 / 128, 6), 1.0])

    def test_float32(self):
        out = summary(build(desc(48000.0, flags=1, bpp=8, ch=2, bits=32),
                            data(pcm([(0.25, -0.5)], bits=32, flt=True))))
        self.assertEqual(out["peak"], [0.25, 0.5])

    def test_float32_little_endian(self):
        out = summary(build(desc(48000.0, flags=1 | 2, bpp=8, ch=2, bits=32),
                            data(pcm([(0.25, -0.5)], bits=32, little=True, flt=True))))
        self.assertEqual(out["peak"], [0.25, 0.5])

    def test_float64(self):
        out = summary(build(desc(48000.0, flags=1, bpp=16, ch=2, bits=64),
                            data(pcm([(0.75, -0.125)], bits=64, flt=True))))
        self.assertEqual(out["peak"], [0.75, 0.125])

    def test_peak_is_null_for_compressed(self):
        out = summary(build(desc(44100.0, fid=b"aac ", flags=0, bpp=0, fpp=1024,
                                 ch=2, bits=0),
                            pakt([100] * 3, fpp=1024),
                            data(bytes(300))))
        self.assertIsNone(out["peak"])

    def test_twenty_four_bit(self):
        # 24-bit stereo: 6 bytes per frame, big-endian.
        audio = (8192).to_bytes(3, "big", signed=True) + \
                (-16384).to_bytes(3, "big", signed=True)
        out = summary(build(desc(96000.0, bpp=6, ch=2, bits=24), data(audio)))
        self.assertEqual(out["peak"], [round(8192 / 2 ** 23, 6),
                                       round(16384 / 2 ** 23, 6)])

    def test_left_justified_20_in_24_bits(self):
        # 20 valid bits left justified in a 3 byte container: shift down by 4.
        value = 0x12345 << 4
        audio = value.to_bytes(3, "big", signed=True)
        out = summary(build(desc(48000.0, bpp=3, ch=1, bits=20), data(audio)))
        self.assertEqual(out["peak"], [round(0x12345 / 2 ** 19, 6)])


class PacketTests(unittest.TestCase):
    def test_fixed_packets_without_pakt(self):
        audio = pcm([(1, 2)] * 10)
        out = summary(build(desc(48000.0, bpp=4, fpp=1, ch=2, bits=16), data(audio)))
        self.assertEqual((out["packets"], out["frames"]), (10, 10))

    def test_variable_packets_use_valid_frames(self):
        # AAC with priming/remainder: valid frames exclude both.
        sizes = [200, 210, 205, 190]
        out = summary(build(
            desc(44100.0, fid=b"aac ", bpp=0, fpp=1024, ch=2, bits=0),
            pakt(sizes, priming=2112, remainder=128),
            data(bytes(sum(sizes)))))
        self.assertEqual(out["packets"], 4)
        self.assertEqual(out["frames"], 4 * 1024 - 2112 - 128)
        self.assertEqual(out["duration"], round(out["frames"] / 44100.0, 6))

    def test_variable_frame_counts_from_pakt(self):
        # frames_per_packet == 0 means each packet entry carries its own count.
        sizes = [10, 20, 30]
        counts = [500, 600, 700]
        out = summary(build(
            desc(48000.0, fid=b"aac ", bpp=0, fpp=0, ch=1, bits=0),
            pakt(sizes, priming=100, remainder=50, fpp=0, frames=counts),
            data(bytes(sum(sizes)))))
        self.assertEqual(out["packets"], 3)
        self.assertEqual(out["frames"], sum(counts) - 100 - 50)

    def test_vlq_multibyte_sizes(self):
        sizes = [127, 128, 16383, 16384, 100000]
        out = summary(build(
            desc(44100.0, fid=b"aac ", bpp=0, fpp=1024, ch=2, bits=0),
            pakt(sizes),
            data(bytes(sum(sizes)))))
        self.assertEqual(out["packets"], len(sizes))
        self.assertEqual(out["frames"], len(sizes) * 1024)


class ChanTests(unittest.TestCase):
    def test_layout_tag_channel_count(self):
        # A layout tag such as kAudioChannelLayoutTag_Stereo packs the count.
        out = summary(build(desc(48000.0), chan(0x00650002), data(pcm([(0, 0)]))))
        self.assertEqual(out["layout_channels"], 2)

    def test_use_channel_descriptions(self):
        out = summary(build(desc(48000.0), chan(0, descs=6), data(pcm([(0, 0)]))))
        self.assertEqual(out["layout_channels"], 6)

    def test_use_channel_bitmap(self):
        out = summary(build(desc(48000.0), chan(0x00010000, bitmap=0b1011),
                            data(pcm([(0, 0)]))))
        self.assertEqual(out["layout_channels"], 3)

    def test_no_chan_chunk(self):
        out = summary(build(desc(48000.0), data(pcm([(0, 0)]))))
        self.assertIsNone(out["layout_channels"])


class InfoTests(unittest.TestCase):
    def test_multiple_pairs(self):
        out = summary(build(desc(48000.0),
                            info([("artist", "Field Unit 3"), ("title", "Dawn")]),
                            data(pcm([(0, 0)]))))
        self.assertEqual(out["info"], {"artist": "Field Unit 3", "title": "Dawn"})

    def test_empty_values_preserved(self):
        out = summary(build(desc(48000.0),
                            info([("comment", ""), ("take", "2")]),
                            data(pcm([(0, 0)]))))
        self.assertEqual(out["info"], {"comment": "", "take": "2"})


class ExampleFileTests(unittest.TestCase):
    def test_field_take(self):
        with open("examples/field_take.caf", "rb") as fh:
            out = summary(fh.read())
        self.assertEqual(out["sample_rate"], 48000.0)
        self.assertEqual(out["frames"], 960)
        self.assertEqual(out["packets"], 960)
        self.assertEqual(out["layout_channels"], 2)
        self.assertEqual(out["peak"], [0.457733, 0.274628])

    def test_voice_memo(self):
        with open("examples/voice_memo.caf", "rb") as fh:
            out = summary(fh.read())
        self.assertEqual(out["sample_rate"], 44100.0)
        self.assertEqual(out["frames"], 46592)
        self.assertEqual(out["packets"], 48)
        self.assertIsNone(out["peak"])
        self.assertEqual(out["layout_channels"], 1)
        self.assertEqual(out["duration"], round(46592 / 44100.0, 6))


if __name__ == "__main__":
    unittest.main()
