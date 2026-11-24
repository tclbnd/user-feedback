import struct
import unittest

from audkit.aud import AudError, ImaAdpcmDecoder, parse

EMPTY = struct.pack("<HIIBB", 22050, 0, 0, 0, 1)
CHUNK_ID = 0x0000DEAF


def chunk(payload, out_size=None, cid=CHUNK_ID):
    """One chunk header plus its compressed bytes (all little-endian)."""
    if out_size is None:
        out_size = len(payload) * 2 * 2          # 16 bit mono
    return struct.pack("<HHI", len(payload), out_size, cid) + payload


def aud(payloads, rate=22050, flags=0x02, codec=99, size=None, out_size=None, trailing=b""):
    body = b"".join(chunk(payload) for payload in payloads)
    if size is None:
        size = len(body)
    if out_size is None:
        out_size = sum(len(p) * 4 for p in payloads)
    return struct.pack("<HIIBB", rate, size, out_size, flags, codec) + body + trailing


class BasicTests(unittest.TestCase):
    def test_rate(self):
        out = parse(EMPTY)
        self.assertEqual((out["sample_rate"], out["chunks"]), (22050, 0))

    def test_codec(self):
        self.assertEqual(parse(EMPTY)["codec"], "westwood_adpcm")

    def test_ima_codec_recognised(self):
        self.assertEqual(parse(aud([b"\x00"])[:12])["codec"], "ima_adpcm")


class HeaderTests(unittest.TestCase):
    def test_length_fields_order(self):
        # First length is the compressed size, second the decompressed size.
        out = parse(aud([b"\x00" * 4], size=12, out_size=16))
        self.assertEqual(out["output_bytes"], 16)
        self.assertEqual(out["data_bytes"], 4)

    def test_flags_mean_stereo_and_sixteen_bit(self):
        self.assertEqual((parse(aud([], flags=0))["channels"], parse(aud([], flags=0))["bits"]), (1, 8))
        self.assertEqual((parse(aud([], flags=1))["channels"], parse(aud([], flags=1))["bits"]), (2, 8))
        self.assertEqual((parse(aud([], flags=2))["channels"], parse(aud([], flags=2))["bits"]), (1, 16))
        self.assertEqual((parse(aud([], flags=3))["channels"], parse(aud([], flags=3))["bits"]), (2, 16))

    def test_header_size_covers_chunk_headers(self):
        data = aud([b"\x00" * 4, b"\x00" * 6])
        out = parse(data)
        self.assertTrue(out["header_size_ok"])
        self.assertEqual(out["data_bytes"], 10)
        self.assertEqual(out["chunks"], 2)

    def test_header_size_mismatch(self):
        data = aud([b"\x00" * 4], size=13)
        self.assertFalse(parse(data)["header_size_ok"])

    def test_frames_and_duration(self):
        # 880 stereo 16-bit frames.
        out = parse(aud([b"\x00" * 440], flags=0x03, out_size=880 * 4))
        self.assertEqual((out["frames"], out["duration"]), (880, "88/2205"))


class ChunkTests(unittest.TestCase):
    def test_chunk_id_is_little_endian(self):
        out = parse(aud([b"\x00" * 4]))
        self.assertEqual((out["chunks"], out["bad_chunk_ids"]), (1, 0))

    def test_wrong_chunk_id_counted(self):
        data = struct.pack("<HIIBB", 22050, 12, 16, 2, 99) + chunk(b"\x00" * 4, cid=0xDEAF0000)
        out = parse(data)
        self.assertEqual((out["chunks"], out["bad_chunk_ids"]), (1, 1))

    def test_only_bytes_covered_by_header_are_audio(self):
        clean = aud([b"\x70\x77\x77\x77"])
        padded = clean + b"\xff" * 20
        self.assertEqual(parse(clean)["last_sample"], parse(padded)["last_sample"])
        self.assertEqual(parse(padded)["data_bytes"], parse(clean)["data_bytes"])
        self.assertFalse(parse(padded)["truncated"])


class TruncationTests(unittest.TestCase):
    def test_file_ends_before_announced_data(self):
        data = aud([b"\x00" * 8], size=16 + 32)
        self.assertTrue(parse(data)["truncated"])

    def test_last_chunk_cut_short(self):
        data = aud([b"\x00" * 8])
        cut = data[:len(data) - 3]
        out = parse(cut)
        self.assertTrue(out["truncated"])
        self.assertEqual(out["chunks"], 1)

    def test_cut_short_chunk_decodes_available_bytes_only(self):
        full = aud([b"\x70\x77\x77\x77"])
        cut = full[:len(full) - 1]
        out = parse(cut)
        self.assertTrue(out["truncated"])
        self.assertEqual(out["last_sample"], ImaAdpcmDecoder().decode(b"\x70\x77\x77")[-1])

    def test_complete_file_not_truncated(self):
        self.assertFalse(parse(aud([b"\x00" * 8]))["truncated"])

    def test_covered_region_ends_inside_chunk_data(self):
        # Eight data bytes in the file, but the header only covers four.
        data = aud([b"\x70\x77\x77\x77\x77\x77\x77\x77"], size=8 + 4)
        out = parse(data)
        self.assertTrue(out["truncated"])
        self.assertEqual(out["last_sample"], ImaAdpcmDecoder().decode(b"\x70\x77\x77\x77")[-1])

    def test_covered_region_ends_inside_chunk_header(self):
        data = aud([b"\x00" * 8], size=4)
        out = parse(data)
        self.assertTrue(out["truncated"])
        self.assertEqual(out["chunks"], 0)

    def test_chunk_id_header_counts_towards_compressed_size(self):
        # 2 chunks x 8 byte headers + 6 data bytes.
        out = parse(aud([b"\x00" * 3, b"\x00" * 3]))
        self.assertTrue(out["header_size_ok"])
        self.assertEqual((out["chunks"], out["data_bytes"]), (2, 6))


class ImaTests(unittest.TestCase):
    def test_decoder_start_state(self):
        decoder = ImaAdpcmDecoder()
        self.assertEqual((decoder.predictor, decoder.index), (0, 0))

    def test_known_samples(self):
        # 0x70 -> low nibble 0 (no change), then high nibble 7 (step 7 -> +11).
        samples = ImaAdpcmDecoder().decode(b"\x70")
        self.assertEqual(samples, [0, 11])

    def test_low_nibble_first(self):
        samples = ImaAdpcmDecoder().decode(b"\x0f\x80")
        self.assertLess(samples[0], samples[2])

    def test_state_carries_across_blocks(self):
        data = b"\x70\x77\x77\x77\xb0\xca"
        decoder = ImaAdpcmDecoder()
        pieces = []
        for byte in data:
            pieces.extend(decoder.decode(bytes([byte])))
        self.assertEqual(pieces, ImaAdpcmDecoder().decode(data))

        whole = parse(aud([data]))
        split = parse(aud([data[:2], data[2:4], data[4:]]))
        self.assertEqual(whole["peak"], split["peak"])
        self.assertEqual(whole["last_sample"], split["last_sample"])

    def test_peak_and_last_sample_reported(self):
        # A rising ramp of full-scale nibbles peaks at the last sample.
        samples = ImaAdpcmDecoder().decode(b"\xf7" * 8)
        out = parse(aud([b"\xf7" * 8]))
        self.assertEqual(out["last_sample"], samples[-1])
        self.assertEqual(out["peak"], "%.6f" % (max(abs(s) for s in samples) / 32768.0))

    def test_peak_is_six_decimals(self):
        self.assertEqual(parse(aud([b"\x00"])).get("peak"), "0.000000")

    def test_other_codecs_have_no_decoded_level(self):
        out = parse(aud([b"\x00" * 4], codec=1))
        self.assertEqual((out["codec"], out["peak"], out["last_sample"]), ("westwood_adpcm", None, None))

    def test_no_audio_no_level(self):
        out = parse(aud([], codec=99))
        self.assertEqual((out["peak"], out["last_sample"]), (None, None))

    def test_example_files(self):
        for path, peak, last in (("examples/eva_ready.aud", "0.618927", -4599),
                                 ("examples/hell_march.aud", "0.279510", -8572)):
            with open(path, "rb") as fh:
                out = parse(fh.read())
            self.assertEqual((out["codec"], out["peak"], out["last_sample"]), ("ima_adpcm", peak, last))
            self.assertTrue(out["header_size_ok"])
            self.assertFalse(out["truncated"])

