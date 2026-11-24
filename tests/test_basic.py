import struct
import unittest

from audkit.aud import AudError, decode_ima, parse

EMPTY = struct.pack("<HIIBB", 22050, 0, 0, 0, 1)
CHUNK_ID = 0x0000DEAF


def header(rate=22050, out_size=0, csize=0, flags=0, codec=1):
    # <HIIBB packs to exactly 12 bytes (the two trailing bytes are unused).
    return struct.pack("<HIIBB", rate, out_size, csize, flags, codec)


def chunk(csize, cout, cid=CHUNK_ID, payload=None):
    head = struct.pack("<HHI", csize, cout, cid)
    if payload is None:
        payload = b"\x00" * csize
    return head + payload[:csize].ljust(csize, b"\x00")


def build(flags=0, codec=1, chunk_list=(), out_size=None, trailing=b""):
    body = b"".join(chunk_list)
    csize = len(body)
    if out_size is None:
        out_size = sum(struct.unpack_from("<H", c, 2)[0] for c in chunk_list)
    data = header(22050, out_size, csize, flags, codec) + body + trailing
    return data


class BasicTests(unittest.TestCase):
    def test_rate(self):
        out = parse(EMPTY)
        self.assertEqual((out["sample_rate"], out["chunks"]), (22050, 0))

    def test_codec(self):
        self.assertEqual(parse(EMPTY)["codec"], "westwood_adpcm")


class HeaderTests(unittest.TestCase):
    def test_too_short(self):
        with self.assertRaises(AudError):
            parse(b"x" * 11)

    def test_zero_rate_rejected(self):
        with self.assertRaises(AudError):
            parse(header(rate=0))

    def test_flag_bits(self):
        # bit 0 = stereo, bit 1 = 16-bit
        self.assertEqual((parse(header(flags=0))["channels"], parse(header(flags=0))["bits"]), (1, 8))
        self.assertEqual((parse(header(flags=1))["channels"], parse(header(flags=1))["bits"]), (2, 8))
        self.assertEqual((parse(header(flags=2))["channels"], parse(header(flags=2))["bits"]), (1, 16))
        stereo16 = parse(header(flags=3))
        self.assertEqual((stereo16["channels"], stereo16["bits"]), (2, 16))

    def test_ima_codec_99(self):
        # Newer tools use codec id 99 for IMA ADPCM, not "type_99".
        self.assertEqual(parse(header(codec=99))["codec"], "ima_adpcm")

    def test_unknown_codec_labeled(self):
        self.assertEqual(parse(header(codec=7))["codec"], "type_7")

    def test_frames_and_duration(self):
        # mono 16-bit: 4 output bytes = 2 frames
        data = build(flags=2, codec=99, chunk_list=[chunk(2, 4)], out_size=4)
        out = parse(data)
        self.assertEqual(out["frames"], 2)
        self.assertEqual(out["duration"], "1/11025")
        # stereo 8-bit: frame size is 2 bytes as well
        data = build(flags=1, codec=1, chunk_list=[chunk(2, 4)], out_size=4)
        self.assertEqual(parse(data)["frames"], 2)


class ChunkTests(unittest.TestCase):
    def test_little_endian_chunk_id_recognized(self):
        data = build(chunk_list=[chunk(4, 16), chunk(4, 16)])
        out = parse(data)
        self.assertEqual(out["chunks"], 2)
        self.assertEqual(out["bad_chunk_ids"], 0)
        self.assertEqual(out["data_bytes"], 8)
        self.assertEqual(out["chunk_output_bytes"], 32)

    def test_big_endian_id_is_bad(self):
        # A big-endian rendering of the id must be flagged, not accepted.
        good = chunk(4, 16)
        bad = struct.pack(">HHI", 4, 16, CHUNK_ID) + b"\x00" * 4
        data = build(chunk_list=[good, bad])
        out = parse(data)
        self.assertEqual(out["chunks"], 2)
        self.assertEqual(out["bad_chunk_ids"], 1)

    def test_header_size_ok(self):
        chunks = [chunk(4, 16), chunk(8, 32)]
        out = parse(build(chunk_list=chunks))
        self.assertTrue(out["header_size_ok"])
        # Lie about the compressed size in the header (field at offset 6).
        raw = build(chunk_list=chunks)
        raw = raw[:6] + struct.pack("<I", 99) + raw[10:]
        self.assertFalse(parse(raw)["header_size_ok"])

    def test_bytes_outside_compressed_size_ignored(self):
        chunks = [chunk(4, 16)]
        # Trailing junk (even something that looks like a chunk) is not audio.
        data = build(chunk_list=chunks, trailing=chunk(4, 16))
        out = parse(data)
        self.assertEqual(out["chunks"], 1)
        self.assertEqual(out["data_bytes"], 4)
        self.assertFalse(out["truncated"])
        self.assertTrue(out["header_size_ok"])


class TruncationTests(unittest.TestCase):
    def test_complete_file_not_truncated(self):
        data = build(codec=99, chunk_list=[chunk(4, 16), chunk(2, 8)])
        self.assertFalse(parse(data)["truncated"])
        self.assertTrue(parse(data)["header_size_ok"])

    def test_last_chunk_payload_cut(self):
        full = build(codec=99, chunk_list=[chunk(4, 16), chunk(8, 32)])
        cut = full[:-3]  # remove part of the last chunk's payload
        out = parse(cut)
        self.assertTrue(out["truncated"])
        self.assertFalse(out["header_size_ok"])
        # The chunk header itself was still fully readable.
        self.assertEqual(out["chunks"], 2)

    def test_chunk_header_cut(self):
        full = build(chunk_list=[chunk(4, 16), chunk(8, 32)])
        cut = full[:-12]  # only four bytes of the last 8-byte chunk header remain
        out = parse(cut)
        self.assertTrue(out["truncated"])
        self.assertEqual(out["chunks"], 1)

    def test_declared_size_runs_past_file(self):
        # Every chunk present is complete, but the header announces more data
        # than the file holds (as the shipped example files do).
        body = chunk(4, 16) + chunk(4, 16)
        raw = header(22050, 32, len(body) + 100, 0, 1) + body
        out = parse(raw)
        self.assertTrue(out["truncated"])
        self.assertFalse(out["header_size_ok"])
        self.assertEqual(out["chunks"], 2)
        self.assertEqual(out["bad_chunk_ids"], 0)


class ImaTests(unittest.TestCase):
    def test_silence_decodes_to_zero(self):
        # Nibble 0 keeps the predictor at 0 and lowers the step index.
        self.assertEqual(decode_ima(b"\x00\x00"), [0, 0, 0, 0])

    def test_known_stream(self):
        # The first bytes of the shipped IMA example (silence lead-in).
        samples = decode_ima(b"\x70\x77\x77\x77")
        self.assertEqual(samples[:3], [0, 11, 41])
        self.assertTrue(all(0 <= s <= 32767 for s in samples))

    def test_peak_and_last_sample_continuous_across_chunks(self):
        payload = bytes((0x77, 0x77, 0x00, 0x12))
        chunks = [chunk(len(payload), len(payload) * 2, payload=payload),
                  chunk(len(payload), len(payload) * 2, payload=payload)]
        data = build(flags=2, codec=99, chunk_list=chunks)
        out = parse(data)
        expected = decode_ima(payload * 2)
        self.assertEqual(out["last_sample"], expected[-1])
        self.assertEqual(out["peak"], round(max(abs(s) for s in expected) / 32768, 6))

    def test_non_ima_has_no_levels(self):
        out = parse(build(codec=1, chunk_list=[chunk(4, 16)]))
        self.assertIsNone(out["peak"])
        self.assertIsNone(out["last_sample"])

    def test_truncated_ima_uses_available_bytes(self):
        payload = bytes((0x77, 0x77, 0x00, 0x12, 0x34, 0x56))
        full = build(flags=2, codec=99, chunk_list=[chunk(6, 24, payload=payload)])
        cut = full[:-2]  # last two payload bytes missing
        out = parse(cut)
        self.assertTrue(out["truncated"])
        expected = decode_ima(payload[:-2])
        self.assertEqual(out["last_sample"], expected[-1])
        self.assertEqual(out["peak"], round(max(abs(s) for s in expected) / 32768, 6))


if __name__ == "__main__":
    unittest.main()
