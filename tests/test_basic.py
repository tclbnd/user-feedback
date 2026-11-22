import struct
import unittest

from cafkit.caf import parse

from cafs import audio, caf, chunk, data, desc, info, pakt


class BasicTests(unittest.TestCase):
    def test_desc(self):
        out = parse(caf(desc(48000.0, b"lpcm", 0, 4, 1, 2, 16)))
        self.assertEqual((out["sample_rate"], out["format"], out["channels"], out["bits"]), (48000.0, "lpcm", 2, 16))

    def test_chunks(self):
        out = parse(caf(desc()))
        self.assertEqual(out["chunks"], ["desc"])

    def test_data_edit_count_is_not_audio(self):
        out = parse(caf(desc(), data(audio(4000), edit=3)))
        self.assertEqual(out["edit_count"], 3)
        self.assertEqual(out["data_bytes"], 4000)
        self.assertEqual((out["packets"], out["frames"]), (1000, 1000))
        self.assertEqual(out["duration"], "10/441")

    def test_data_unknown_size_runs_to_eof(self):
        body = struct.pack(">I", 0) + audio(4000)
        out = parse(caf(desc(), chunk(b"data", body, size=-1)))
        self.assertEqual(out["data_bytes"], 4000)
        self.assertEqual((out["packets"], out["frames"]), (1000, 1000))

    def test_pakt_valid_frames_and_multibyte_sizes(self):
        aac = desc(44100.0, b"aac ", 0, 0, 1024, 1, 0)
        sizes = [150, 549, 300]  # all need multi-byte varints
        out = parse(caf(aac, pakt(sizes=sizes, valid=2345, priming=2112, remainder=727),
                        data(audio(sum(sizes)))))
        self.assertEqual(out["packets"], 3)
        self.assertEqual(out["frames"], 2345)  # not 3 * 1024
        self.assertEqual((out["priming"], out["remainder"]), (2112, 727))
        self.assertEqual(out["max_packet_bytes"], 549)
        self.assertEqual(out["max_packet_frames"], 1024)
        self.assertEqual(out["duration"], "67/1260")

    def test_info(self):
        out = parse(caf(desc(), info([("title", "Harbour dawn"), ("recorded date", "2026-11-21 09:30")])))
        self.assertEqual(out["info"], {"title": "Harbour dawn", "recorded date": "2026-11-21 09:30"})
