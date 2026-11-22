import os
import unittest

from cafkit.caf import CafError, chunks, parse, varint

from cafs import asc, audio, caf, data, data_with_size, desc, info, kuki, pakt

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


class VarintTests(unittest.TestCase):
    def test_multibyte_roundtrip(self):
        from cafs import varint as encode
        for value in (0, 1, 127, 128, 16383, 16384, 549, 406757):
            got, pos = varint(encode(value), 0)
            self.assertEqual(got, value)
            self.assertEqual(pos, len(encode(value)))

    def test_truncated(self):
        with self.assertRaises(CafError):
            varint(b"\x81", 0)


class ChunkTests(unittest.TestCase):
    def test_size_minus_one_runs_to_eof(self):
        raw = caf(desc(), data_with_size(b"abcdefgh", -1))
        found = chunks(raw)
        ctype, body, size = found[1]
        self.assertEqual(ctype, "data")
        self.assertEqual(size, len(raw) - body)

    def test_declared_size_past_eof_is_clamped(self):
        raw = caf(desc(), data_with_size(audio(10), 10 ** 9))
        found = chunks(raw)
        ctype, body, size = found[1]
        self.assertEqual(size, len(raw) - body)

    def test_normal_size_unchanged(self):
        raw = caf(desc(), data(audio(8), edit=7))
        found = chunks(raw)
        self.assertEqual(found[1][2], 12)


class PcmTests(unittest.TestCase):
    def test_edit_count_not_audio(self):
        # 40 audio bytes + 4 byte edit count; bpp=4 => exactly 10 packets,
        # never 11 from counting the edit count as audio.
        out = parse(caf(desc(bpp=4, fpp=1), data(audio(40), edit=3)))
        self.assertEqual(out["edit_count"], 3)
        self.assertEqual(out["data_bytes"], 40)
        self.assertEqual(out["packets"], 10)
        self.assertEqual(out["frames"], 10)

    def test_truncated_after_power_loss(self):
        raw = caf(desc(bpp=4, fpp=1), data_with_size(audio(20), 10 ** 9))
        out = parse(raw)
        self.assertEqual(out["data_bytes"], 20)
        self.assertEqual(out["packets"], 5)

    def test_streaming_size_minus_one(self):
        raw = caf(desc(bpp=2, fpp=1), data_with_size(audio(30), -1))
        out = parse(raw)
        self.assertEqual(out["data_bytes"], 30)
        self.assertEqual(out["packets"], 15)


class InfoTests(unittest.TestCase):
    def test_info_strings_big_endian_count(self):
        out = parse(caf(desc(), info([("title", "Harbour dawn"), ("artist", "me")])))
        self.assertEqual(out["info"], {"title": "Harbour dawn", "artist": "me"})


class PaktTests(unittest.TestCase):
    def test_variable_sizes_and_frames(self):
        sizes = [150, 549, 200]
        frames = [1024, 991, 1024]
        out = parse(caf(desc(44100.0, b"aac ", 0, 0, 0, 1, 0),
                        pakt(sizes, frames, valid=2900, priming=2112, remainder=731),
                        data(b"\0" * sum(sizes))))
        self.assertEqual(out["packets"], 3)
        self.assertEqual(out["max_packet_bytes"], 549)
        self.assertEqual(out["max_packet_frames"], 1024)
        self.assertEqual(out["priming"], 2112)
        self.assertEqual(out["remainder"], 731)
        # valid frames exclude priming/remainder
        self.assertEqual(out["frames"], 2900)
        self.assertEqual(out["duration"], "29/441")

    def test_frames_geometry_when_valid_zero(self):
        sizes = [10, 10, 10]
        out = parse(caf(desc(48000.0, b"aac ", 0, 0, 1024, 1, 0),
                        pakt(sizes, valid=0, priming=2112, remainder=731),
                        data(b"\0" * 30)))
        self.assertEqual(out["frames"], 3 * 1024 - 2112 - 731)
        self.assertEqual(out["max_packet_bytes"], 10)
        self.assertEqual(out["max_packet_frames"], 1024)


class CookieTests(unittest.TestCase):
    def test_lc_cookie(self):
        out = parse(caf(desc(44100.0, b"aac ", 0, 0, 1024, 1, 0), kuki(asc(2, 4, 1))))
        self.assertEqual(out["aac"], {"object_type": 2, "sbr": False, "ps": False,
                                      "sample_rate": 44100, "output_sample_rate": 44100,
                                      "channel_config": 1})

    def test_he_aac_v1(self):
        out = parse(caf(desc(48000.0, b"aac ", 0, 0, 2048, 1, 0), kuki(asc(2, he=1))))
        self.assertEqual(out["aac"]["object_type"], 2)
        self.assertTrue(out["aac"]["sbr"])
        self.assertFalse(out["aac"]["ps"])
        self.assertEqual(out["aac"]["sample_rate"], 24000)
        self.assertEqual(out["aac"]["output_sample_rate"], 48000)

    def test_he_aac_v2(self):
        out = parse(caf(desc(48000.0, b"aac ", 0, 0, 2048, 1, 0), kuki(asc(2, he=2))))
        self.assertTrue(out["aac"]["sbr"])
        self.assertTrue(out["aac"]["ps"])
        self.assertEqual(out["aac"]["sample_rate"], 24000)
        self.assertEqual(out["aac"]["output_sample_rate"], 48000)


class ExampleFilesTests(unittest.TestCase):
    def test_voice_memo(self):
        with open(os.path.join(EXAMPLES, "voice_memo.caf"), "rb") as fh:
            out = parse(fh.read())
        self.assertEqual(out["format"], "aac")
        self.assertEqual(out["data_bytes"], 139800)
        self.assertEqual(out["packets"], 400)
        self.assertEqual(out["frames"], 406757)
        self.assertEqual(out["max_packet_bytes"], 549)
        self.assertEqual(out["max_packet_frames"], 1024)
        self.assertEqual(out["priming"], 2112)
        self.assertEqual(out["remainder"], 731)
        self.assertEqual(out["info"]["title"], "Standup 11-21")
        self.assertEqual(out["aac"]["sample_rate"], 44100)

    def test_field(self):
        with open(os.path.join(EXAMPLES, "field.caf"), "rb") as fh:
            out = parse(fh.read())
        self.assertEqual(out["format"], "lpcm")
        self.assertTrue(out["float"])
        self.assertTrue(out["little_endian"])
        self.assertEqual(out["data_bytes"], 192000)
        self.assertEqual(out["packets"], 24000)
        self.assertEqual(out["frames"], 24000)
        self.assertEqual(out["info"], {"title": "Harbour dawn"})


if __name__ == "__main__":
    unittest.main()
