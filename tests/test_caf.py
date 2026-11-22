import os
import struct
import unittest

from cafkit.caf import CafError, parse

from cafs import audio, caf, chan, chunk, desc, info, pakt


AAC_DESC = desc(44100.0, b"aac ", 0, 0, 1024, 1, 0)
PCM_DESC = desc(48000.0, b"lpcm", 3, 4, 1, 2, 16)


def asc(bits):
    """Pack a bit string MSB-first, padding the tail to whole bytes."""
    bits = bits.replace(" ", "")
    bits += "0" * (-len(bits) % 8)
    return int(bits, 2).to_bytes(len(bits) // 8, "big")


class TestChunks(unittest.TestCase):
    def test_negative_size_runs_to_eof(self):
        body = b"\0\0\0\0" + audio(32)
        raw = caf(PCM_DESC, chunk(b"data", body, size=-1))
        out = parse(raw)
        self.assertEqual(out["chunks"], ["desc", "data"])
        self.assertEqual(out["edit_count"], 0)
        self.assertEqual(out["data_bytes"], 32)
        self.assertEqual(out["packets"], 8)
        self.assertEqual(out["frames"], 8)

    def test_data_size_past_eof_is_clipped(self):
        raw = caf(PCM_DESC, chunk(b"data", b"\0\0\0\0" + audio(16), size=4096))
        out = parse(raw)
        self.assertEqual(out["data_bytes"], 16)
        self.assertEqual(out["packets"], 4)

    def test_truncated_chunk_is_clipped(self):
        raw = caf(PCM_DESC, chunk(b"free", b"1234", size=99), chunk(b"free", b"ab"))
        out = parse(raw)
        self.assertEqual(out["chunks"], ["desc", "free"])

    def test_no_desc(self):
        with self.assertRaises(CafError):
            parse(caf())

    def test_short_trailing_header_is_ignored(self):
        out = parse(caf(PCM_DESC) + b"dat")
        self.assertEqual(out["chunks"], ["desc"])


class TestPCM(unittest.TestCase):
    def test_edit_count_is_not_audio(self):
        raw = caf(PCM_DESC, chunk(b"data", b"\0\0\0\x07" + audio(400)))
        out = parse(raw)
        self.assertEqual(out["edit_count"], 7)
        self.assertEqual(out["data_bytes"], 400)
        self.assertEqual(out["packets"], 100)
        self.assertEqual(out["frames"], 100)
        self.assertEqual(out["duration"], "1/480")

    def test_multichannel_packets(self):
        d = desc(44100.0, b"lpcm", 2, 6, 1, 2, 24)
        raw = caf(d, chunk(b"data", b"\0\0\0\0" + audio(60)))
        out = parse(raw)
        self.assertEqual((out["bytes_per_packet"], out["data_bytes"], out["packets"]), (6, 60, 10))
        self.assertEqual(out["frames"], 10)

    def test_no_data(self):
        out = parse(caf(PCM_DESC))
        self.assertIsNone(out["data_bytes"])
        self.assertIsNone(out["packets"])
        self.assertIsNone(out["frames"])
        self.assertIsNone(out["duration"])

    def test_unusable_sample_rate(self):
        for rate in (0.0, float("nan"), float("inf")):
            out = parse(caf(desc(rate), chunk(b"data", b"\0\0\0\0" + audio(8))))
            self.assertEqual(out["frames"], 2)
            self.assertIsNone(out["duration"])


class TestPacketTable(unittest.TestCase):
    def test_variable_bytes_max_packet_and_priming(self):
        sizes = [150, 400, 549, 300]
        raw = caf(AAC_DESC, pakt(sizes, valid=1000, priming=2112, remainder=731),
                  chunk(b"data", b"\0\0\0\0" + audio(sum(sizes))))
        out = parse(raw)
        self.assertEqual(out["packets"], 4)
        self.assertEqual(out["frames"], 1000)
        self.assertEqual(out["priming"], 2112)
        self.assertEqual(out["remainder"], 731)
        self.assertEqual(out["max_packet_bytes"], 549)
        self.assertEqual(out["max_packet_frames"], 1024)
        self.assertEqual(out["duration"], "10/441")

    def test_frames_without_valid_frames(self):
        sizes = [100, 100, 100]
        raw = caf(AAC_DESC, pakt(sizes, priming=2112, remainder=731),
                  chunk(b"data", b"\0\0\0\0" + audio(sum(sizes))))
        out = parse(raw)
        self.assertEqual(out["frames"], 3 * 1024 - 2112 - 731)

    def test_variable_frames(self):
        d = desc(48000.0, b"aac ", 0, 0, 0, 2, 0)
        raw = caf(d, pakt(sizes=[100, 120, 140], frames=[1024, 900, 500], valid=2400))
        out = parse(raw)
        self.assertEqual(out["packets"], 3)
        self.assertEqual(out["frames"], 2400)
        self.assertEqual(out["max_packet_frames"], 1024)
        self.assertEqual(out["max_packet_bytes"], 140)

    def test_variable_frames_derived(self):
        d = desc(48000.0, b"aac ", 0, 0, 0, 2, 0)
        raw = caf(d, pakt(sizes=[100, 120, 140], frames=[1024, 900, 500]))
        self.assertEqual(parse(raw)["frames"], 2424)

    def test_constant_bytes_variable_frames(self):
        d = desc(48000.0, b"alac", 0, 34, 0, 2, 16)
        raw = caf(d, pakt(frames=[4096, 2048, 1000]))
        out = parse(raw)
        self.assertEqual(out["packets"], 3)
        self.assertEqual(out["frames"], 7144)
        self.assertEqual(out["max_packet_frames"], 4096)
        self.assertEqual(out["max_packet_bytes"], 34)

    def test_partial_table(self):
        body = pakt(sizes=[100, 200, 300])[12:-1] + b"\x81"
        raw = caf(AAC_DESC, chunk(b"pakt", body))
        out = parse(raw)
        self.assertEqual(out["packets"], 2)
        self.assertEqual(out["max_packet_bytes"], 200)

    def test_unfinalised_packet_count(self):
        body = pakt(sizes=[100, 200, 300], packets=0)[12:]
        raw = caf(AAC_DESC, chunk(b"pakt", body))
        out = parse(raw)
        self.assertEqual(out["packets"], 3)
        self.assertEqual(out["max_packet_bytes"], 300)

    def test_three_byte_varint(self):
        sizes = [100, 20000, 300]
        raw = caf(AAC_DESC, pakt(sizes), chunk(b"data", b"\0\0\0\0" + audio(sum(sizes))))
        out = parse(raw)
        self.assertEqual(out["max_packet_bytes"], 20000)
        self.assertEqual(out["packets"], 3)

    def test_negative_data_size_with_packet_table(self):
        sizes = [100, 100]
        raw = caf(AAC_DESC, pakt(sizes, valid=2048, priming=1024, remainder=0),
                  b"data" + struct.pack(">q", -1) + b"\0\0\0\0" + audio(200))
        out = parse(raw)
        self.assertEqual(out["data_bytes"], 200)
        self.assertEqual(out["packets"], 2)
        self.assertEqual(out["frames"], 1024)

    def test_data_before_packet_table(self):
        raw = caf(AAC_DESC, chunk(b"data", b"\0\0\0\0" + audio(200)),
                  pakt([100, 100], valid=2000))
        out = parse(raw)
        self.assertEqual(out["packets"], 2)
        self.assertEqual(out["frames"], 2000)


class TestInfo(unittest.TestCase):
    def test_big_endian_count(self):
        raw = caf(desc(), info([("title", "Standup 11-21"), ("recorded date", "2026-11-21 09:30")]))
        out = parse(raw)
        self.assertEqual(out["info"], {"title": "Standup 11-21", "recorded date": "2026-11-21 09:30"})

    def test_empty_info(self):
        self.assertEqual(parse(caf(desc(), info([])))["info"], {})

    def test_more_entries_than_strings(self):
        body = b"\0\0\0\x05key\0value\0"
        out = parse(caf(desc(), chunk(b"info", body)))
        self.assertEqual(out["info"], {"key": "value"})

    def test_utf8(self):
        out = parse(caf(desc(), info([("artist", "海边")])))
        self.assertEqual(out["info"]["artist"], "海边")


class TestAAC(unittest.TestCase):
    def cookie(self, dsi):
        return caf(desc(44100.0, b"aac ", 0, 0, 1024, 2, 0), chunk(b"kuki", dsi))

    def test_lc(self):
        out = parse(self.cookie(b"\x12\x10"))
        self.assertEqual(out["aac"], {"object_type": 2, "sbr": False, "ps": False, "sample_rate": 44100,
                                      "output_sample_rate": 44100, "channel_config": 2})

    def test_esds(self):
        dsi = b"\x12\x10"
        dcd = b"\x40\x15\x00\x00\x00\x00\x01\xf4\x00\x00\x01\xf4\x00" + b"\x05" + bytes([len(dsi)]) + dsi
        es = b"\x00\x00\x00" + b"\x04" + bytes([len(dcd)]) + dcd
        esds = b"\x03" + bytes([len(es)]) + es
        out = parse(self.cookie(esds))
        self.assertEqual(out["aac"]["object_type"], 2)
        self.assertEqual(out["aac"]["sample_rate"], 44100)

    def test_esds_with_id_url_and_ocr(self):
        dsi = b"\x12\x10"
        dcd = b"\x40\x15\x00\x00\x00\x00\x01\xf4\x00\x00\x01\xf4\x00" + b"\x05" + bytes([len(dsi)]) + dsi
        es = b"\x00\x00\xe0" + b"\x00\x05" + b"\x03abc" + b"\x00\x07" + b"\x04" + bytes([len(dcd)]) + dcd
        esds = b"\x03" + bytes([len(es)]) + es
        out = parse(self.cookie(esds))
        self.assertEqual(out["aac"]["object_type"], 2)

    def test_he_aac(self):
        # AOT 5, core 22050 mono, extension 44100, then AOT 2 with a zero GA config
        bits = "00101" "0111" "0001" "0100" "00010" "000"
        out = parse(self.cookie(asc(bits)))
        self.assertEqual(out["aac"]["object_type"], 2)
        self.assertTrue(out["aac"]["sbr"])
        self.assertFalse(out["aac"]["ps"])
        self.assertEqual(out["aac"]["sample_rate"], 22050)
        self.assertEqual(out["aac"]["output_sample_rate"], 44100)

    def test_he_aac_v2(self):
        bits = "11101" "0111" "0001" "0100" "00010" "000"
        out = parse(self.cookie(asc(bits)))
        self.assertTrue(out["aac"]["sbr"])
        self.assertTrue(out["aac"]["ps"])
        self.assertEqual(out["aac"]["object_type"], 2)
        self.assertEqual(out["aac"]["output_sample_rate"], 44100)

    def test_implicit_sbr(self):
        core = "00010" "0110" "0001" "000"  # AAC LC 24000 mono
        ext = "01010110111" "00101" "1" "0111"  # sync 0x2B7, AOT 5, sbr, 22050
        out = parse(self.cookie(asc(core + ext)))
        self.assertTrue(out["aac"]["sbr"])
        self.assertEqual(out["aac"]["sample_rate"], 24000)
        self.assertEqual(out["aac"]["output_sample_rate"], 22050)

    def test_non_aac_has_no_cookie_summary(self):
        raw = caf(PCM_DESC, chunk(b"kuki", b"\x12\x10"))
        self.assertIsNone(parse(raw)["aac"])


class TestChannelLayout(unittest.TestCase):
    def test_chan(self):
        out = parse(caf(desc(), chan(tag=(101 << 16) | 2, bitmap=3)))
        self.assertEqual(out["channel_layout"], {"tag": (101 << 16) | 2, "bitmap": 3})

    def test_no_chan(self):
        self.assertIsNone(parse(caf(desc()))["channel_layout"])


class TestExamples(unittest.TestCase):
    def example(self, name):
        path = os.path.join(os.path.dirname(__file__), os.pardir, "examples", name)
        with open(path, "rb") as fh:
            return parse(fh.read())

    def test_field_recording(self):
        out = self.example("field.caf")
        self.assertEqual(out["format"], "lpcm")
        self.assertEqual(out["chunks"], ["desc", "info", "data", "free"])
        self.assertEqual(out["edit_count"], 0)
        self.assertEqual(out["data_bytes"], 192000)
        self.assertEqual(out["packets"], 24000)
        self.assertEqual(out["frames"], 24000)
        self.assertEqual(out["duration"], "1/2")
        self.assertEqual((out["max_packet_bytes"], out["max_packet_frames"]), (8, 1))
        self.assertEqual(out["info"], {"title": "Harbour dawn"})

    def test_voice_memo(self):
        out = self.example("voice_memo.caf")
        self.assertEqual(out["format"], "aac")
        self.assertEqual(out["edit_count"], 0)
        self.assertEqual(out["data_bytes"], 139800)
        self.assertEqual(out["packets"], 400)
        self.assertEqual(out["frames"], 406757)
        self.assertEqual((out["priming"], out["remainder"]), (2112, 731))
        self.assertEqual(out["duration"], "406757/44100")
        self.assertEqual((out["max_packet_bytes"], out["max_packet_frames"]), (549, 1024))
        self.assertEqual(out["info"], {"title": "Standup 11-21", "recorded date": "2026-11-21 09:30"})
        self.assertEqual(out["channel_layout"], {"tag": (100 << 16) | 1, "bitmap": 0})
        self.assertEqual(out["aac"], {"object_type": 2, "sbr": False, "ps": False, "sample_rate": 44100,
                                      "output_sample_rate": 44100, "channel_config": 1})


if __name__ == "__main__":
    unittest.main()