import struct
import unittest

from audkit.aud import parse

EMPTY = struct.pack("<HIIBB", 22050, 0, 0, 0, 1)

STEP_TABLE = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37,
    41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173,
    190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658,
    724, 796, 875, 962, 1058, 1164, 1280, 1408, 1548, 1703, 1873, 2060,
    2266, 2492, 2741, 3015, 3316, 3648, 4012, 4414, 4855, 5340, 5874,
    6461, 7107, 7818, 8600, 9460, 10406, 11446, 12591, 13850, 15235,
    16758, 18433, 20277, 22304, 24534, 26986, 29684, 32652,
)
INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8)


def reference_ima(payload, channels):
    """Independent IMA ADPCM reference: one continuous stream."""
    predictor = [0] * channels
    index = [0] * channels
    samples = []
    cursor = 0
    for byte in payload:
        for nibble in (byte & 0x0F, byte >> 4):
            step = STEP_TABLE[index[cursor]]
            diff = step >> 3
            if nibble & 4:
                diff += step
            if nibble & 2:
                diff += step >> 1
            if nibble & 1:
                diff += step >> 2
            if nibble & 8:
                predictor[cursor] -= diff
            else:
                predictor[cursor] += diff
            predictor[cursor] = max(-32768, min(32767, predictor[cursor]))
            index[cursor] = max(0, min(88, index[cursor] + INDEX_TABLE[nibble]))
            samples.append(predictor[cursor])
            cursor = (cursor + 1) % channels
    return samples


def chunk(csize, cout, cid=0xDEAF, number=0, payload=b""):
    return struct.pack("<HHHH", csize, cout, cid, number) + payload


def aud_file(rate, comp, out, flags, codec, body, extra=b""):
    return struct.pack("<HIIBB", rate, comp, out, flags, codec) + body + extra


class BasicTests(unittest.TestCase):
    def test_rate(self):
        out = parse(EMPTY)
        self.assertEqual((out["sample_rate"], out["chunks"]), (22050, 0))

    def test_codec(self):
        self.assertEqual(parse(EMPTY)["codec"], "westwood_adpcm")

    def test_codec_99_is_ima_adpcm(self):
        data = aud_file(22050, 0, 0, 0, 99, b"")
        self.assertEqual(parse(data)["codec"], "ima_adpcm")

    def test_header_length_field_order(self):
        # field at offset 2 is the compressed size, offset 6 the output size
        body = chunk(4, 16, payload=b"\x12\x34\x56\x78")
        data = aud_file(22050, 12, 16, 0, 99, body)
        info = parse(data)
        self.assertEqual(info["data_bytes"], 4)
        self.assertEqual(info["output_bytes"], 16)
        self.assertEqual(info["chunk_output_bytes"], 16)
        self.assertTrue(info["header_size_ok"])

    def test_chunk_header_is_little_endian_with_u16_id_and_number(self):
        body = chunk(2, 8, cid=0xDEAF, number=0x0001, payload=b"\x00\x00")
        data = aud_file(22050, 10, 8, 0, 99, body)
        info = parse(data)
        self.assertEqual(info["chunks"], 1)
        self.assertEqual(info["bad_chunk_ids"], 0)

    def test_bad_chunk_id_counted(self):
        body = chunk(2, 8, cid=0x1234, payload=b"\x00\x00")
        data = aud_file(22050, 10, 8, 0, 99, body)
        self.assertEqual(parse(data)["bad_chunk_ids"], 1)

    def test_trailing_bytes_beyond_compressed_size_are_not_audio(self):
        payload = b"\x12\x34\x56\x78"
        body = chunk(4, 16, payload=payload)
        data = aud_file(22050, 12, 16, 0, 99, body, extra=b"\xde\xad\xbe\xef")
        info = parse(data)
        self.assertEqual(info["chunks"], 1)
        self.assertEqual(info["data_bytes"], 4)
        self.assertFalse(info["truncated"])
        self.assertTrue(info["header_size_ok"])

    def test_compressed_size_short_of_file_marks_truncation(self):
        # Two chunk headers; the header's compressed size ends inside chunk 2.
        body = chunk(4, 16, payload=b"\x01\x02\x03\x04")
        body += chunk(8, 32, payload=b"\x05\x06\x07\x08\x09\x0a\x0b\x0c")
        comp = 8 + 4 + 8 + 4  # full chunk 1, full header 2, 4 of its 8 payload
        data = aud_file(22050, comp, 48, 0, 99, body)
        info = parse(data)
        self.assertTrue(info["truncated"])
        self.assertEqual(info["chunks"], 2)
        self.assertEqual(info["data_bytes"], 8)  # 4 full + 4 partial bytes
        # the header's covered bytes still line up; the damage is truncation
        self.assertTrue(info["header_size_ok"])
        # only 8 audio bytes => 16 nibbles => 16 decoded samples
        self.assertEqual(info["last_sample"],
                         reference_ima(b"\x01\x02\x03\x04\x05\x06\x07\x08", 1)[-1])

    def test_header_runs_past_end_of_file_is_truncated(self):
        body = chunk(8, 32, payload=b"\x01\x02\x03\x04")  # only 4 bytes on disk
        data = aud_file(22050, 16, 32, 0, 99, body)
        info = parse(data)
        self.assertTrue(info["truncated"])
        self.assertEqual(info["data_bytes"], 4)

    def test_partial_chunk_header_is_truncated(self):
        data = aud_file(22050, 12, 0, 0, 99, b"\x00\x02\x00\x08")
        info = parse(data)
        self.assertTrue(info["truncated"])
        self.assertEqual(info["chunks"], 0)

    def test_ima_decoding_peak_and_last_sample(self):
        payload = bytes(range(256))
        body = chunk(len(payload), len(payload) * 4, payload=payload)
        data = aud_file(22050, 8 + len(payload), len(payload) * 4, 0, 99, body)
        info = parse(data)
        expected = reference_ima(payload, 1)
        self.assertEqual(info["last_sample"], expected[-1])
        self.assertEqual(info["peak"],
                         float("%.6f" % (max(abs(s) for s in expected) / 32768.0)))

    def test_ima_state_continues_across_chunks(self):
        first = b"\x3a\x71\xf2"
        second = b"\x9c\x04\xe6"
        body = chunk(len(first), len(first) * 4, number=0, payload=first)
        body += chunk(len(second), len(second) * 4, number=1, payload=second)
        data = aud_file(22050, 16 + len(first) + len(second),
                        (len(first) + len(second)) * 4, 2, 99, body)
        info = parse(data)
        expected = reference_ima(first + second, 2)
        self.assertEqual(info["chunks"], 2)
        self.assertEqual(info["last_sample"], expected[-1])
        self.assertEqual(info["peak"],
                         float("%.6f" % (max(abs(s) for s in expected) / 32768.0)))

    def test_non_ima_has_null_peak_and_last_sample(self):
        body = chunk(4, 16, payload=b"\x12\x34\x56\x78")
        data = aud_file(22050, 12, 16, 0, 1, body)
        info = parse(data)
        self.assertIsNone(info["peak"])
        self.assertIsNone(info["last_sample"])


if __name__ == "__main__":
    unittest.main()
