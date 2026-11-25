import os
import struct
import unittest

from audkit.aud import AudError, ImaAdpcmDecoder, parse

CHUNK_ID = 0x0000DEAF


def header(rate=22050, compressed=0, output=0, flags=2, codec=99):
    # <H I I B B: rate, compressed size (first length), output size (second).
    return struct.pack("<HIIBB", rate, compressed, output, flags, codec)


def chunk(csize, cout, payload=b"", cid=CHUNK_ID):
    # Chunk header is entirely little-endian; id is 0x0000DEAF.
    return struct.pack("<HHI", csize, cout, cid) + payload


class HeaderTests(unittest.TestCase):
    def test_length_field_order(self):
        # compressed=18 (one 8-byte header + 10 payload), output=20.
        body = chunk(10, 20, payload=b"\x01" * 10)
        out = parse(header(compressed=len(body), output=20) + body)
        self.assertEqual(out["data_bytes"], 10)
        self.assertEqual(out["output_bytes"], 20)
        self.assertTrue(out["header_size_ok"])

    def test_flags_bits(self):
        # bit 0 = stereo, bit 1 = 16-bit.
        self.assertEqual((parse(header(flags=0, output=4))["channels"],
                          parse(header(flags=0, output=4))["bits"]), (1, 8))
        self.assertEqual((parse(header(flags=1, output=4))["channels"],
                          parse(header(flags=1, output=4))["bits"]), (2, 8))
        self.assertEqual((parse(header(flags=2, output=4))["channels"],
                          parse(header(flags=2, output=4))["bits"]), (1, 16))
        out = parse(header(flags=3, output=8))
        self.assertEqual((out["channels"], out["bits"]), (2, 16))

    def test_codecs(self):
        self.assertEqual(parse(header(codec=99))["codec"], "ima_adpcm")
        self.assertEqual(parse(header(codec=1))["codec"], "westwood_adpcm")
        self.assertEqual(parse(header(codec=7))["codec"], "type_7")

    def test_rejects_non_aud(self):
        with self.assertRaises(AudError):
            parse(b"\x00" * 5)
        with self.assertRaises(AudError):
            parse(header(rate=0))

    def test_chunk_id_is_little_endian(self):
        body = chunk(1, 2, payload=b"\x05")
        out = parse(header(compressed=len(body), output=2) + body)
        self.assertEqual(out["chunks"], 1)
        self.assertEqual(out["bad_chunk_ids"], 0)
        # The same bytes read big-endian would be a different, bad id.
        bad = chunk(1, 2, payload=b"\x05", cid=0x1234)
        out = parse(header(compressed=len(bad), output=2) + bad)
        self.assertEqual(out["bad_chunk_ids"], 1)


class TruncationTests(unittest.TestCase):
    def test_incomplete_last_chunk_payload(self):
        # Header announces a 10-byte payload but only 2 bytes are present.
        body = chunk(10, 20, payload=b"\x01\x02")
        out = parse(header(compressed=8 + 10, output=20) + body)
        self.assertTrue(out["truncated"])
        self.assertEqual(out["chunks"], 0)
        self.assertIsNone(out["last_sample"])
        self.assertIsNone(out["peak"])
        self.assertFalse(out["header_size_ok"])

    def test_partial_chunk_header(self):
        full = chunk(2, 4, payload=b"\x07\x00")
        out = parse(header(compressed=len(full) + 8, output=12)
                    + full + b"\x02\x00\x04")
        self.assertTrue(out["truncated"])
        self.assertEqual(out["chunks"], 1)

    def test_header_smaller_than_file_ignores_trailing_bytes(self):
        body = chunk(2, 4, payload=b"\x07\x00")
        out = parse(header(compressed=len(body), output=4)
                    + body + b"\x99" * 32)
        self.assertFalse(out["truncated"])
        self.assertEqual(out["chunks"], 1)
        self.assertEqual(out["data_bytes"], 2)
        self.assertTrue(out["header_size_ok"])

    def test_complete_file_not_truncated(self):
        body = chunk(2, 4, payload=b"\x07\x00") + chunk(2, 4, payload=b"\x12\x34")
        out = parse(header(compressed=len(body), output=8) + body)
        self.assertFalse(out["truncated"])
        self.assertEqual(out["chunks"], 2)


class ImaDecoderTests(unittest.TestCase):
    def test_hand_checked_samples(self):
        dec = ImaAdpcmDecoder().feed(bytes([0x07]))
        # nibble 7: 89 + 11 + 22 + 44 = 166; index 0 + 8 = 8.
        # nibble 0: step[8]=176, 166 + 22 = 188; index 8 - 1 = 7.
        self.assertEqual(dec.last_sample, 188)
        self.assertEqual(dec.step_index, 7)
        self.assertEqual(dec.peak_magnitude, 188)
        dec = ImaAdpcmDecoder().feed(bytes([0x00]))
        # nibble 0: 89 >> 3 = 11; index 0 - 1 clamps to 0.
        # nibble 0: 11 + 11 = 22.
        self.assertEqual(dec.last_sample, 22)
        self.assertEqual(dec.step_index, 0)

    def test_clamping(self):
        # Enough positive max-magnitude nibbles to drive the predictor up.
        dec = ImaAdpcmDecoder().feed(b"\x77" * 200)
        self.assertEqual(dec.last_sample, 32767)
        self.assertEqual(dec.step_index, 88)
        # High nibble 0xF is negative: drives the predictor to the floor.
        dec = ImaAdpcmDecoder().feed(b"\xff" * 200)
        self.assertEqual(dec.last_sample, -32768)

    def test_peak_is_fraction_of_32768(self):
        dec = ImaAdpcmDecoder().feed(bytes([0x07]))
        out = parse(header(compressed=9, output=2)
                    + chunk(1, 2, payload=b"\x07"))
        self.assertEqual(out["peak"], round(dec.peak_magnitude / 32768.0, 6))

    def test_state_continues_across_chunks(self):
        p1, p2 = b"\x07\x00", b"\x12\x34"
        body = chunk(2, 4, payload=p1) + chunk(2, 4, payload=p2)
        out = parse(header(compressed=len(body), output=8) + body)
        reference = ImaAdpcmDecoder().feed(p1 + p2)
        self.assertEqual(out["last_sample"], reference.last_sample)
        # A per-chunk reset would give a different, wrong final sample.
        reset = ImaAdpcmDecoder().feed(p2)
        self.assertNotEqual(out["last_sample"], reset.last_sample)

    def test_no_samples_gives_null_fields(self):
        out = parse(header(codec=99))
        self.assertIsNone(out["peak"])
        self.assertIsNone(out["last_sample"])
        body = chunk(0, 0)
        out = parse(header(compressed=len(body), output=0) + body)
        self.assertIsNone(out["peak"])
        self.assertIsNone(out["last_sample"])

    def test_westwood_adpcm_has_no_decode_fields(self):
        out = parse(header(codec=1, compressed=0, output=100, flags=0))
        self.assertIsNone(out["peak"])
        self.assertIsNone(out["last_sample"])
        self.assertEqual((out["channels"], out["bits"]), (1, 8))


class ExampleFileTests(unittest.TestCase):
    def setUp(self):
        here = os.path.dirname(__file__)
        self.data = {}
        for name in ("eva_ready.aud", "hell_march.aud"):
            with open(os.path.join(here, "..", "examples", name), "rb") as fh:
                self.data[name] = fh.read()

    def test_eva_ready(self):
        out = parse(self.data["eva_ready.aud"])
        self.assertEqual(out["sample_rate"], 22050)
        self.assertEqual((out["channels"], out["bits"]), (1, 16))
        self.assertEqual(out["codec"], "ima_adpcm")
        self.assertEqual(out["chunks"], 6)
        self.assertEqual(out["bad_chunk_ids"], 0)
        self.assertEqual(out["data_bytes"], 3000)
        self.assertTrue(out["header_size_ok"])
        self.assertEqual(out["output_bytes"], 12000)
        self.assertEqual(out["chunk_output_bytes"], 12000)
        self.assertEqual(out["frames"], 6000)
        self.assertFalse(out["truncated"])
        self.assertEqual(out["last_sample"], 32767)
        self.assertEqual(out["peak"], 1.0)

    def test_hell_march(self):
        out = parse(self.data["hell_march.aud"])
        self.assertEqual((out["channels"], out["bits"]), (1, 16))
        self.assertEqual(out["codec"], "ima_adpcm")
        self.assertEqual(out["chunks"], 10)
        self.assertEqual(out["data_bytes"], 10000)
        self.assertTrue(out["header_size_ok"])
        self.assertEqual(out["frames"], 20000)
        self.assertFalse(out["truncated"])
        self.assertEqual(out["last_sample"], -32768)
        self.assertEqual(out["peak"], 1.0)


if __name__ == "__main__":
    unittest.main()
