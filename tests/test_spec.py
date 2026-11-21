"""Regression tests for the qoaformat.org conformance fixes.

Each test maps to one of the reported decoder defects: reversed frame header
fields, 16-bit rather than 24-bit sample rates, channel-major slice ordering,
padding samples in the last partial slice, truncated dequantization rounding
and zero sample counts for streamed files.
"""
import struct
import unittest

from qoakit.qoa import (DEQUANT, FRAME_LEN, QoaError, decode_frame, parse)

from files import frame, qoa, song


def make_frame_bytes(n, ch=1, rate=44100):
    """A valid frame of n samples/ch with zeroed LMS state and slice words."""
    slices = (n + 19) // 20
    fsize = 8 + 16 * ch + 8 * slices * ch
    return bytes([ch]) + rate.to_bytes(3, "big") + \
        struct.pack(">HH", n, fsize) + bytes(fsize - 8)


class HeaderTests(unittest.TestCase):
    def test_frame_header_samples_before_size(self):
        # Frame layout is u8 channels, u24 rate, u16 frame_samples, u16
        # frame_size: the two 16-bit fields must not be swapped.
        f, end = decode_frame(frame(2, 44100, 5120), 0)
        self.assertEqual((f["samples"], end), (5120, 4136))

    def test_sample_rate_is_24bit(self):
        for rate in (96000, 192000, 16777215):
            f, _ = decode_frame(make_frame_bytes(7, rate=rate), 0)
            self.assertEqual(f["rate"], rate)

    def test_96k_not_read_as_low_word(self):
        # Reading only the low 16 bits of 96000 yields 30464.
        f, _ = decode_frame(make_frame_bytes(7, rate=96000), 0)
        self.assertNotEqual(f["rate"], 30464)


class SliceTests(unittest.TestCase):
    def test_stereo_slices_are_slice_major(self):
        # Two stereo slices with distinct words per channel/slice.  Decoding
        # must consume channel 0 then channel 1 of slice 0 before slice 1.
        fsamp = 40
        head = bytes([2]) + (44100).to_bytes(3, "big") + \
            struct.pack(">HH", fsamp, 8 + 32 + 32)
        head += bytes(32)  # zeroed LMS state for both channels
        words = b""
        for sf in range(4):
            words += ((sf + 1) << 60).to_bytes(8, "big")
        f, _ = decode_frame(head + words, 0)
        # All-zero LMS history makes predictor 0, so the first sample of each
        # channel equals its slice word's first dequantized residual.
        self.assertEqual(f["pcm"][0][0], DEQUANT[1][0])
        self.assertEqual(f["pcm"][1][0], DEQUANT[2][0])
        self.assertEqual(f["pcm"][0][20], DEQUANT[3][0])
        self.assertEqual(f["pcm"][1][20], DEQUANT[4][0])

    def test_partial_last_slice_emits_no_padding(self):
        for n in (1, 19, 21, 39, 41, 5119):
            f, _ = decode_frame(make_frame_bytes(n), 0)
            self.assertEqual(len(f["pcm"][0]), n)

    def test_partial_frame_total_sample_count(self):
        out = parse(song(12347, ch=2, rate=48000))
        self.assertEqual(out["samples"], 12347)
        self.assertEqual(out["decoded_samples"], 12347)


class DequantTests(unittest.TestCase):
    def test_table_matches_reference_rounding(self):
        # qoa.h qoa_dequant_tab: round(scalefactor * quant / 4), half away
        # from zero -- plain int() truncation gets the .5 entries wrong.
        self.assertEqual(DEQUANT[1], (5, -5, 18, -18, 32, -32, 49, -49))
        self.assertEqual(DEQUANT[2], (16, -16, 53, -53, 95, -95, 147, -147))
        self.assertEqual(DEQUANT[15],
                         (1536, -1536, 5120, -5120, 9216, -9216, 14336, -14336))
        self.assertNotEqual(DEQUANT[1][2], int(7 * 10 / 4))  # 18 vs truncated 17

    def test_mono_matches_expectation_on_synthetic_file(self):
        out = parse(song(5120, ch=1, rate=44100))
        self.assertEqual(out["channels"], 1)
        self.assertEqual(out["decoded_samples"], 5120)
        self.assertEqual(len(out["pcm_crc32"]), 8)


class StreamingTests(unittest.TestCase):
    def _stream(self, total_samples, **kw):
        blob = song(total_samples, **kw)
        return blob[:4] + struct.pack(">I", 0) + blob[8:]

    def test_streaming_samples_are_sum_of_frames(self):
        out = parse(self._stream(7001, ch=1, rate=96000))
        self.assertTrue(out["streaming"])
        self.assertEqual(out["samples"], 7001)
        self.assertEqual(out["decoded_samples"], 7001)
        self.assertEqual(out["sample_rate"], 96000)
        self.assertNotEqual(out["duration"], "0")

    def test_streaming_stereo_partial_last_frame(self):
        out = parse(self._stream(11000, ch=2, rate=48000))
        self.assertEqual((out["samples"], out["frames"]), (11000, 3))

    def test_streaming_may_change_format(self):
        blob = qoa([frame(1, 44100, 40, seed=1),
                    frame(2, 48000, 60, seed=2)], 0)
        out = parse(blob)
        self.assertEqual(out["samples"], 100)


class CrcTests(unittest.TestCase):
    def test_crc_is_interleaved_little_endian(self):
        # One mono sample with zeroed state, slice word all zero:
        # dequant[0][0] = 1, so PCM is the LE encoding of 1.
        blob = b"qoaf" + struct.pack(">I", 1) + make_frame_bytes(1)
        import zlib
        want = "%08x" % (zlib.crc32(struct.pack("<h", 1)) & 0xFFFFFFFF)
        self.assertEqual(parse(blob)["pcm_crc32"], want)

    def test_stereo_crc_uses_interleaved_order(self):
        # Two channels, one sample each, zero state, distinct scalefactors.
        head = bytes([2]) + (44100).to_bytes(3, "big") + \
            struct.pack(">HH", 1, 8 + 32 + 16) + bytes(32)
        head += (1 << 60).to_bytes(8, "big")
        head += (2 << 60).to_bytes(8, "big")
        blob = b"qoaf" + struct.pack(">I", 1) + head
        import zlib
        pcm = struct.pack("<hh", DEQUANT[1][0], DEQUANT[2][0])
        want = "%08x" % (zlib.crc32(pcm) & 0xFFFFFFFF)
        self.assertEqual(parse(blob)["pcm_crc32"], want)


class ValidationTests(unittest.TestCase):
    def test_bad_frame_size_rejected(self):
        f = bytearray(frame(2, 44100, 40))
        f[6] = (f[6] + 1) & 0xFF
        with self.assertRaises(QoaError):
            parse(b"qoaf" + struct.pack(">I", 40) + bytes(f))

    def test_format_change_rejected_for_nonstreaming(self):
        blob = qoa([frame(1, 44100, 40), frame(2, 44100, 40)], 80)
        with self.assertRaises(QoaError):
            parse(blob)

    def test_truncation_rejected(self):
        with self.assertRaises(QoaError):
            parse(song(100)[:-5])

    def test_max_frame_samples_enforced(self):
        f = bytearray(frame(1, 44100, 40))
        struct.pack_into(">H", f, 4, FRAME_LEN + 1)
        with self.assertRaises(QoaError):
            decode_frame(bytes(f), 0)


if __name__ == "__main__":
    unittest.main()
