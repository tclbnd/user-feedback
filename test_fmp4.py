#!/usr/bin/env python3
"""
Acceptance tests for fmp4.py against the hand-crafted files from mkmp4.py.

Checks, mapped to the task requirements:
  1. box-level byte spans (ftyp/moov/moof/traf/tfhd/tfdt/trun/mdat);
  2. per-track, per-run sample byte ranges incl. the fragment's second run
     (audio must never be read through the video run's offset);
  3. DTS from tfdt + cumulative durations; PTS with signed (negative) cts,
     so B-frame samples appear in presentation order on the timeline;
  4. keyframe flags from per-sample / first-sample / tfhd / trex defaults;
  5. tfhd and trex default sample fields;
  6. seamless continuation vs. timeline gaps across fragments;
  7. truncated mdat -> locatable error, complete tracks/fragments survive;
  8. identical index under arbitrary read chunk sizes;
  9. repeated seek-based playback positioning on a freshly opened file;
 10. largesize (size==1) and box-to-EOF (size==0) headers.
"""
import hashlib
import io
import os
import struct
import unittest

import fmp4
import mkmp4
from fmp4 import (ChunkedReader, Fmp4Error, index_bytes, index_stream,
                  read_sample)
from mkmp4 import (AUDIO_TID, AUDIO_TS, FRAG1_AUDIO, FRAG1_VIDEO,
                   FRAG2_AUDIO, FRAG2_VIDEO, FRAG3_AUDIO, FRAG3_VIDEO,
                   TFDT, VIDEO_TID, VIDEO_TS, build_file)

OUTDIR = os.path.join(os.path.dirname(__file__), "out")


def track(index, tid):
    return next(t for t in index["tracks"] if t["id"] == tid)


def by_trun(t):
    table = {}
    for s in t["decode_order"]:
        table.setdefault((s["fragment_seq"], s["trun"]), []).append(s)
    return table


def expected_video_timeline():
    # (seq, run, idx, dts, cts, pts, duration, keyframe)
    rows = []
    for seq, runs, base in (
            (1, FRAG1_VIDEO, TFDT[1][VIDEO_TID]),
            (2, FRAG2_VIDEO, TFDT[2][VIDEO_TID]),
            (3, FRAG3_VIDEO, TFDT[3][VIDEO_TID])):
        dts = base
        for ri, (samples, opts) in enumerate(runs):
            for si, (dur, size, cts, fl) in enumerate(samples):
                if dur is None:
                    dur = 1000  # tfhd default for frag 1 video
                if fl is None:
                    # missing flags resolve to tfhd/trex defaults: only
                    # frag1 run0 sample1 hits the tfhd NONSYNC default;
                    # frag2/3 trafs have no tfhd defaults, so trex (sync)
                    # wins unless first_sample_flags overrides sample 0
                    key = not (seq == 1 and ri == 0 and si == 1)
                else:
                    key = fl == 0
                rows.append((seq, ri, si, dts, cts, dts + cts, dur, key))
                dts += dur
    return rows


class FullFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.spec = build_file()
        cls.index = index_bytes(cls.data, "full.fmp4")
        os.makedirs(OUTDIR, exist_ok=True)
        with open(os.path.join(OUTDIR, "full.fmp4"), "wb") as f:
            f.write(cls.data)

    def test_01_box_layout_byte_spans(self):
        types = [(b["type"], b["start"], b["end"]) for b in self.index["boxes"]]
        names = [t[0] for t in types]
        # top-level ordering
        self.assertEqual([n for n in names if n in ("ftyp", "moov", "moof",
                                                    "mdat")][:2],
                         ["ftyp", "moov"])
        self.assertEqual(names.count("moof"), 3)
        self.assertEqual(names.count("mdat"), 3)
        self.assertIn("trun", names)
        self.assertIn("tfdt", names)
        self.assertIn("tfhd", names)
        self.assertIn("trex", names)
        # every span is inside the file and internally consistent
        for b in self.index["boxes"]:
            self.assertGreaterEqual(b["start"], 0)
            self.assertLessEqual(b["end"], self.index["size"])
            self.assertEqual(b["end"] - b["start"], b["size"])
            self.assertLess(b["header_end"], b["end"])
        # declared boxes really contain those fourcc bytes
        for b in self.index["boxes"]:
            self.assertEqual(self.data[b["start"] + 4:b["start"] + 8],
                             b["type"].encode())
        # mdat payload windows match the box layout
        for m, b in zip(self.index["mdat"],
                        [x for x in self.index["boxes"] if x["type"] == "mdat"]):
            self.assertEqual(m["payload_start"], b["header_end"])
            self.assertEqual(m["payload_end"], b["end"])

    def test_02_run_offsets_match_builder_and_stay_in_mdat(self):
        v = track(self.index, VIDEO_TID)
        a = track(self.index, AUDIO_TID)
        for seq in (1, 2, 3):
            loc = dict((tid, {ri: offs for tid0, ri, offs in
                              self.spec[seq]["locations"] if tid0 == tid})
                       for tid in (VIDEO_TID, AUDIO_TID))
            for tid, t in ((VIDEO_TID, v), (AUDIO_TID, a)):
                for (s, ri), samples in by_trun(t).items():
                    if s != seq:
                        continue
                    # run start must equal the builder's first offset;
                    # subsequent offsets are derived from resolved sizes
                    self.assertEqual(samples[0]["offset"], loc[tid][ri][0],
                                     (seq, tid, ri))
                    expected = [samples[0]["offset"]]
                    for prev in samples[:-1]:
                        expected.append(expected[-1] + prev["size"])
                    self.assertEqual([x["offset"] for x in samples], expected)
                    # consecutive samples are contiguous inside the run
                    for x, y in zip(samples, samples[1:]):
                        self.assertEqual(y["offset"], x["end"])
        # no two sample ranges overlap even across interleaved tracks
        ranges = sorted((s["offset"], s["end"])
                        for t in self.index["tracks"]
                        for s in t["decode_order"])
        for (o1, e1), (o2, e2) in zip(ranges, ranges[1:]):
            self.assertLessEqual(e1, o2)

    def test_03_second_run_audio_not_read_as_video(self):
        v = track(self.index, VIDEO_TID)
        a = track(self.index, AUDIO_TID)
        # fragment 1 video run 1 starts strictly after video run 0, while
        # audio run 0 is a separate range: payloads carry identifying labels
        v0 = next(s for s in v["decode_order"]
                  if s["fragment_seq"] == 1 and s["trun"] == 0)
        a0 = next(s for s in a["decode_order"]
                  if s["fragment_seq"] == 1 and s["trun"] == 0)
        a1 = next(s for s in a["decode_order"]
                  if s["fragment_seq"] == 1 and s["trun"] == 1)
        self.assertNotEqual(v0["offset"], a0["offset"])
        self.assertTrue(a1["offset"] > a0["offset"] + a0["size"])
        for s, label in ((v0, b"v1r0s0"), (a0, b"a1r0s0"), (a1, b"a1r1s0")):
            raw = self.data[s["offset"]:s["end"]]
            self.assertTrue(raw.startswith(label),
                            (label, raw[:8]))
            self.assertEqual(hashlib.sha256(raw).hexdigest(), s["sha256"])
    def test_04_dts_pts_and_bframe_reorder(self):
        v = track(self.index, VIDEO_TID)
        got = [(s["fragment_seq"], s["trun"], s["sample"], s["dts"],
                s["cts"], s["pts"], s["duration"], s["keyframe"])
               for s in v["decode_order"]]
        # fragment 3's video run 1 sample is not truncated in the full file
        self.assertEqual(got, expected_video_timeline())
        # decode order dts is monotonic; pts with negative cts is not
        dts = [s["dts"] for s in v["decode_order"]]
        self.assertEqual(dts, sorted(dts))
        pts = [s["pts"] for s in v["decode_order"]]
        self.assertNotEqual(pts, sorted(pts))
        # the presentation timeline is sorted and maps back to the right
        # bytes (frame with dts 1000/pts 0 displays first)
        timeline_pts = [s["pts"] for s in v["timeline"]]
        self.assertEqual(timeline_pts, sorted(timeline_pts))
        first = v["timeline"][0]
        self.assertEqual((first["dts"], first["pts"]), (1000, 0))
        raw = self.data[first["offset"]:first["end"]]
        self.assertEqual(raw.rstrip(b"\x00"), b"v1r0s1")

    def test_05_keyframe_flag_defaults(self):
        v = track(self.index, VIDEO_TID)
        # frag1 run0: no per-sample flags; sample0 uses first_sample_flags
        # (sync), others fall through to tfhd default NONSYNC
        r0 = [s for s in v["decode_order"]
              if s["fragment_seq"] == 1 and s["trun"] == 0]
        self.assertEqual([s["keyframe"] for s in r0], [True, False])
        # frag1 run1: explicit per-sample flags NONSYNC then SYNC
        r1 = [s for s in v["decode_order"]
              if s["fragment_seq"] == 1 and s["trun"] == 1]
        self.assertEqual([s["keyframe"] for s in r1], [False, True])
        # frag2/3: no tfhd defaults -> trex flags 0 => keyframe, except the
        # samples explicitly flagged NONSYNC
        for s in v["decode_order"]:
            if s["fragment_seq"] >= 2:
                expect_nonsync = (s["flags"] & 0x00010000) != 0
                self.assertEqual(s["keyframe"], not expect_nonsync)
        # audio defaults: all keyframes
        a = track(self.index, AUDIO_TID)
        self.assertTrue(all(s["keyframe"] for s in a["decode_order"]))

    def test_06_tfhd_and_trex_default_fields(self):
        v = track(self.index, VIDEO_TID)
        a = track(self.index, AUDIO_TID)
        # video frag1 run0 sizes/durations come from the tfhd default 40/1000
        for s in v["decode_order"]:
            if s["fragment_seq"] == 1 and s["trun"] == 0:
                self.assertEqual(s["size"], 40)
                self.assertEqual(s["duration"], 1000)
        # trex fallback for audio frag1 run0 sample2 (size 20, dur 1024)
        trex_sample = next(s for s in a["decode_order"]
                           if s["fragment_seq"] == 1 and s["trun"] == 0
                           and s["sample"] == 2)
        self.assertEqual(trex_sample["size"], 20)
        self.assertEqual(trex_sample["duration"], 1024)
        self.assertEqual(a["trex_defaults"]["duration"], 1024)
        self.assertEqual(a["trex_defaults"]["size"], 20)
        self.assertEqual(v["trex_defaults"]["size"], 64)

    def test_07_continuation_and_gaps(self):
        v = track(self.index, VIDEO_TID)
        a = track(self.index, AUDIO_TID)
        vf, af = v["fragments"], a["fragments"]
        self.assertEqual([f["cont"] for f in vf], [None, True, True])
        self.assertEqual([f["gap"] for f in vf], [None, 0, 0])
        # audio: seamless, then a 1024-unit hole (5120 -> 6144), then seamless
        self.assertEqual([f["cont"] for f in af], [None, False, True])
        self.assertEqual(af[1]["gap"], TFDT[2][AUDIO_TID] - 5120)
        self.assertEqual(af[2]["gap"], 0)
        # cont is stamped on the first sample of each fragment only
        self.assertIsNone(next(s for s in v["decode_order"]
                               if s["fragment_seq"] == 1)["cont"])
        self.assertTrue(next(s for s in v["decode_order"]
                             if s["fragment_seq"] == 2)["cont"])
        later = [s for s in v["decode_order"]
                 if s["fragment_seq"] == 2 and s["sample"] > 0]
        self.assertTrue(all(s["cont"] is None for s in later))

    def test_08_timescale_milliseconds(self):
        v = track(self.index, VIDEO_TID)
        a = track(self.index, AUDIO_TID)
        self.assertEqual(v["timescale"], VIDEO_TS)
        self.assertEqual(a["timescale"], AUDIO_TS)
        s = next(x for x in v["decode_order"] if x["dts"] == 1000)
        self.assertEqual(s["dts_ms"], 1000.0)
        self.assertEqual(s["pts_ms"], 0.0)
        a0 = a["decode_order"][0]
        self.assertEqual(a0["dts_ms"], 0.0)
        self.assertEqual(a["decode_order"][1]["dts_ms"],
                         round(1024 * 1000.0 / AUDIO_TS, 3))

    def test_09_dts_accumulates_across_runs(self):
        # second trun inside the same traf continues the DTS walk
        a = track(self.index, AUDIO_TID)
        f1 = [s for s in a["decode_order"] if s["fragment_seq"] == 1]
        self.assertEqual([s["dts"] for s in f1],
                         [0, 1024, 2048, 3072, 4096])
        v = track(self.index, VIDEO_TID)
        vf1 = [s for s in v["decode_order"] if s["fragment_seq"] == 1]
        self.assertEqual([s["dts"] for s in vf1], [0, 1000, 2000, 3000])


class TruncationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.full_data, full_spec = build_file()
        cls.data, cls.spec = build_file(truncate=True)
        cls.index = index_bytes(cls.data, "truncated.fmp4")

    def test_10_locatable_truncation_error(self):
        self.assertEqual(self.index["size"], self.spec["_cut_offset"])
        self.assertTrue(self.index["errors"])
        e = next(e for e in self.index["errors"]
                 if e["code"] == "truncated_mdat")
        self.assertEqual(e["track_id"], VIDEO_TID)
        self.assertEqual(e["fragment_seq"], 3)
        self.assertEqual(e["trun"], 1)
        self.assertEqual(e["sample"], 0)
        self.assertEqual(e["missing"], 40)
        self.assertEqual(e["offset"], self.spec["_cut_offset"])
        self.assertEqual(e["requested_end"], e["offset"] + 40)
        self.assertEqual(e["offset"] + e["missing"],
                         self.spec["_cut_offset"] + 40)

    def test_11_complete_tracks_and_fragments_survive(self):
        v = track(self.index, VIDEO_TID)
        a = track(self.index, AUDIO_TID)
        # video: frags 1 (4) + 2 (2) + frag3 run0 (1) indexed
        self.assertEqual(len(v["decode_order"]), 7)
        self.assertEqual(len(v["fragments"]), 3)
        # audio: frag1 5 + frag2 2 = 7 (frag3 mdat is beyond the cut)
        self.assertEqual(len(a["decode_order"]), 7)
        self.assertEqual(len(a["fragments"]), 2)
        # surviving samples still hash against their exact byte ranges
        for t in self.index["tracks"]:
            for s in t["decode_order"]:
                raw = self.data[s["offset"]:s["end"]]
                self.assertEqual(len(raw), s["size"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), s["sha256"])
        # and they match the untruncated file byte-for-byte
        full_index = index_bytes(self.full_data)
        for t, tf in zip(self.index["tracks"], full_index["tracks"]):
            self.assertEqual(
                [(s["offset"], s["size"], s["dts"], s["pts"])
                 for s in t["decode_order"]],
                [(s["offset"], s["size"], s["dts"], s["pts"])
                 for s in tf["decode_order"][:len(t["decode_order"])]])


class ChunkIndependenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, _ = build_file()

    def test_12_index_identical_for_all_chunk_sizes(self):
        baseline = index_bytes(self.data)
        for chunk in (1, 2, 3, 7, 16, 64, 257, 4096):
            reader = ChunkedReader(io.BytesIO(self.data), chunk)
            got = index_stream(reader, "chunked-%d" % chunk)
            self.assertEqual(got["tracks"], baseline["tracks"])
            self.assertEqual(got["boxes"], baseline["boxes"])
            self.assertEqual(got["mdat"], baseline["mdat"])
            self.assertEqual(got["errors"], baseline["errors"])
            self.assertEqual(got["size"], baseline["size"])
            self.assertEqual(got["sha256"], baseline["sha256"])
            self.assertLessEqual(reader.max_read, chunk)

    def test_13_truncated_file_under_one_byte_reads(self):
        data, _ = build_file(truncate=True)
        reader = ChunkedReader(io.BytesIO(data), 1)
        got = index_stream(reader)
        self.assertTrue(any(e["code"] == "truncated_mdat"
                            for e in got["errors"]))
        self.assertEqual(len(track(got, AUDIO_TID)["decode_order"]), 7)

    def test_14_repeated_playback_seek_only(self):
        # simulate repeat/seek playback on a new stream with a tiny read
        # buffer: positioning must not depend on buffered read history
        os.makedirs(OUTDIR, exist_ok=True)
        path = os.path.join(OUTDIR, "full.fmp4")
        with open(path, "wb") as f:
            f.write(self.data)
        with open(path, "rb") as f:
            idx = index_stream(ChunkedReader(f, 5))
        order = [4, 0, 7, 2, 7, 0, 6]
        for i in order:
            with open(path, "rb") as f:
                s = idx["tracks"][0]["timeline"][i]
                raw = read_sample(ChunkedReader(f, 3), s)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), s["sha256"])
        # seek to an audio sample from a different fragment each time
        a = idx["tracks"][1]
        for i in (8, 0, 5, 3):
            with open(path, "rb") as f:
                s = a["decode_order"][i]
                raw = read_sample(f, s)
                self.assertTrue(raw[:1] in (b"a", b"\x00"))


class BoxHeaderTests(unittest.TestCase):
    def _wrap(self, payload_cells):
        # cells: (fourcc, payload bytes)
        return b"".join(struct.pack(">I", 8 + len(p)) + t + p
                        for t, p in payload_cells)

    def test_15_largesize_header(self):
        # 4-byte size(=1) + fourcc + 8-byte largesize(=24) + 8 payload bytes
        data = b"\x00\x00\x00\x01mdat" + struct.pack(">Q", 24) + b"\x00" * 8
        boxes = list(fmp4.iter_boxes(io.BytesIO(data), 0, len(data)))
        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0].type, b"mdat")
        self.assertEqual(boxes[0].header_end, 16)
        self.assertEqual(boxes[0].end, 24)
        self.assertEqual(len(data), 24)

    def test_16_size_zero_runs_to_scope_end(self):
        data = b"\x00\x00\x00\x00free" + b"padding"
        boxes = list(fmp4.iter_boxes(io.BytesIO(data), 0, len(data)))
        self.assertEqual(boxes[0].end, len(data))

    def test_17_truncated_box_header_reported(self):
        with self.assertRaises(Fmp4Error) as ctx:
            list(fmp4.iter_boxes(io.BytesIO(b"md"), 0, 2))
        self.assertEqual(ctx.exception.code, "truncated_box")
        # index level: a cut final top-level box header is locatable
        result = index_bytes(b"mda")
        self.assertTrue(any(e["code"] == "truncated_box"
                            for e in result["errors"]))
        # an impossibly small declared size is an invalid_box_size
        result2 = index_bytes(struct.pack(">I", 3) + b"ftyp" + b"\x00" * 8)
        self.assertTrue(any(e["code"] == "invalid_box_size"
                            for e in result2["errors"]))

    def test_18_box_running_past_scope_rejected(self):
        data = struct.pack(">I", 40) + b"moof" + b"\x00" * 12
        with self.assertRaises(Fmp4Error) as ctx:
            list(fmp4.iter_boxes(io.BytesIO(data), 0, len(data)))
        self.assertEqual(ctx.exception.code, "truncated_box")


if __name__ == "__main__":
    unittest.main(verbosity=2)
