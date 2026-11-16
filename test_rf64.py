#!/usr/bin/env python3
"""Tests for the RF64/WAVEFORMATEXTENSIBLE JSON subset reader.

Fixtures are intentionally small.  They exercise the 64-bit size path
(``riff_size`` sentinel plus ``ds64_size``), 24-bit samples carried in a
32-bit container, unknown/extra blocks, non-contiguous reads, and truncated
tails.  Every check compares per-channel values so a misaligned stride shows
up as a concrete frame offset.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

import rf64


def make_doc(channels=2, riff_size=None, ds64_size=None, frames=4, **extra):
    """Build an interleaved fixture whose sample value encodes frame/channel."""
    data = [f * 100 + c for f in range(frames) for c in range(channels)]
    doc = {"channels": channels, "sample_rate": 48000,
           "bytes_per_sample": 2, "interleaved": data, "requests": []}
    if riff_size is not None:
        doc["riff_size"] = riff_size
    if ds64_size is not None:
        doc["ds64_size"] = ds64_size
    doc.update(extra)
    return doc


def channel_values(frames, channels, channel):
    return [f * 100 + channel for f in range(frames)]


class FrameCountTests(unittest.TestCase):
    def test_ds64_size_is_64_bit_not_truncated(self):
        # A small file simulating the 64-bit path: the ds64 length is far
        # beyond 2^32 and must survive unmasked as frames_total.
        doc = make_doc(channels=2, riff_size=0xFFFFFFFF, frames=2)
        doc["ds64_size"] = 5_000_000_000
        doc["requests"] = [{"id": "head", "channel": 0,
                            "start_frame": 0, "count": 1}]
        out = rf64.process(doc)
        self.assertEqual(out["frames_total"], 5_000_000_000)
        self.assertEqual(out["results"][0]["samples"], [0])

    def test_ds64_used_when_riff_size_is_sentinel(self):
        # 64-bit chunk-size path: the 0xFFFFFFFF placeholder must not be
        # mistaken for the real length.
        doc = make_doc(channels=6, riff_size=0xFFFFFFFF, ds64_size=3, frames=9)
        self.assertEqual(rf64.process(doc)["frames_total"], 3)

    def test_ds64_ignored_when_riff_size_is_real(self):
        doc = make_doc(channels=2, riff_size=3, ds64_size=8, frames=9)
        self.assertEqual(rf64.process(doc)["frames_total"], 3)

    def test_declared_frames_can_exceed_backing_store(self):
        doc = make_doc(channels=2, riff_size=8, frames=5)
        self.assertEqual(rf64.process(doc)["frames_total"], 8)

    def test_ds64_frames_reported_even_when_file_is_truncated(self):
        # Declared size wins for frames_total; reads still stop at real data.
        doc = make_doc(channels=2, riff_size=0xFFFFFFFF, ds64_size=10, frames=2)
        doc["requests"] = [{"id": "tail", "channel": 0,
                            "start_frame": 1, "count": 50}]
        out = rf64.process(doc)
        self.assertEqual(out["frames_total"], 10)
        self.assertEqual(out["results"][0]["samples"], [100])

    def test_declared_frames_can_under_run_backing_store(self):
        # Samples beyond the declared data chunk are not part of it.
        doc = make_doc(channels=2, riff_size=2, frames=6)
        out = rf64.process(doc)
        self.assertEqual(out["frames_total"], 2)
        self.assertEqual(out["results"], [])

    def test_missing_size_falls_back_to_available_frames(self):
        doc = make_doc(channels=2, frames=5)
        self.assertEqual(rf64.process(doc)["frames_total"], 5)

    def test_partial_trailing_frame_is_not_a_frame(self):
        doc = make_doc(channels=2, riff_size=0xFFFFFFFF, ds64_size=0, frames=0)
        doc["interleaved"] = [1, 2, 3]  # one and a half frames
        self.assertEqual(rf64.process(doc)["frames_total"], 0)


class RequestTests(unittest.TestCase):
    def test_channel_deinterleave_at_offset(self):
        doc = make_doc(channels=6, riff_size=0xFFFFFFFF, ds64_size=4, frames=4)
        doc["requests"] = [
            {"id": "c2", "channel": 2, "start_frame": 1, "count": 2},
            {"id": "c5", "channel": 5, "start_frame": 0, "count": 10},
        ]
        res = rf64.process(doc)["results"]
        self.assertEqual(res[0], {"id": "c2", "channel": 2, "start_frame": 1,
                                  "samples": [102, 202]})
        self.assertEqual(res[1]["samples"], channel_values(4, 6, 5))

    def test_all_returns_raw_interleaved_span(self):
        doc = make_doc(channels=2, riff_size=0xFFFFFFFF, ds64_size=4, frames=4)
        doc["requests"] = [{"id": "a", "channel": "all",
                            "start_frame": 1, "count": 2}]
        self.assertEqual(rf64.process(doc)["results"][0]["samples"],
                         [100, 101, 200, 201])

    def test_non_contiguous_requests_keep_order(self):
        doc = make_doc(channels=2, riff_size=0xFFFFFFFF, ds64_size=5, frames=5)
        doc["requests"] = [
            {"id": "late", "channel": 0, "start_frame": 4, "count": 1},
            {"id": "first", "channel": 0, "start_frame": 0, "count": 1},
            {"id": "middle", "channel": 1, "start_frame": 2, "count": 2},
        ]
        results = rf64.process(doc)["results"]
        self.assertEqual([r["id"] for r in results], ["late", "first", "middle"])
        self.assertEqual(results[0]["samples"], [400])
        self.assertEqual(results[1]["samples"], [0])
        self.assertEqual(results[2]["samples"], [201, 301])

    def test_count_clips_at_declared_eof(self):
        doc = make_doc(channels=2, riff_size=0xFFFFFFFF, ds64_size=4, frames=9)
        doc["requests"] = [{"id": "x", "channel": 1,
                            "start_frame": 3, "count": 99}]
        res = rf64.process(doc)["results"][0]
        self.assertEqual(res["samples"], [301])

    def test_count_zero_is_an_empty_but_valid_read(self):
        doc = make_doc(channels=2, riff_size=4, frames=4)
        doc["requests"] = [{"id": "z", "channel": "all",
                            "start_frame": 2, "count": 0}]
        self.assertEqual(rf64.process(doc)["results"][0]["samples"], [])

    def test_start_at_or_past_eof_reports_eof_and_others_run(self):
        doc = make_doc(channels=2, riff_size=0xFFFFFFFF, ds64_size=4, frames=4)
        doc["requests"] = [
            {"id": "edge", "channel": 0, "start_frame": 4, "count": 1},
            {"id": "neg", "channel": 0, "start_frame": -1, "count": 1},
            {"id": "far", "channel": 0, "start_frame": 999999, "count": 1},
            {"id": "ok", "channel": 0, "start_frame": 0, "count": 2},
        ]
        results = rf64.process(doc)["results"]
        self.assertEqual(results[0], {"id": "edge", "error": "eof"})
        self.assertEqual(results[1], {"id": "neg", "error": "eof"})
        self.assertEqual(results[2], {"id": "far", "error": "eof"})
        self.assertEqual(results[3]["samples"], [0, 100])
        self.assertEqual([r["id"] for r in results],
                         ["edge", "neg", "far", "ok"])

    def test_negative_start_does_not_wrap_into_array(self):
        doc = make_doc(channels=2, riff_size=4, frames=4)
        doc["requests"] = [{"id": "n", "channel": "all",
                            "start_frame": -2, "count": 4}]
        self.assertEqual(rf64.process(doc)["results"],
                         [{"id": "n", "error": "eof"}])

    def test_truncated_backing_store_clips_without_padding(self):
        doc = make_doc(channels=2, riff_size=8, frames=3)
        doc["requests"] = [
            {"id": "tail", "channel": "all", "start_frame": 2, "count": 6},
            {"id": "gap", "channel": 0, "start_frame": 6, "count": 2},
        ]
        results = rf64.process(doc)["results"]
        self.assertEqual(results[0]["samples"], [200, 201])
        self.assertEqual(results[1]["samples"], [])
        self.assertEqual(results[1]["start_frame"], 6)

    def test_tail_truncated_mid_frame_drops_partial_frame(self):
        # Declared 5 frames / 6 channels, but the file stops after 2 frames
        # plus 4 samples of the third.  Frame 2 is incomplete, so channel 0
        # must not report a value that channels 4/5 do not have.
        doc = make_doc(channels=6, riff_size=0xFFFFFFFF, ds64_size=5, frames=5)
        doc["interleaved"] = doc["interleaved"][:2 * 6 + 4]
        doc["requests"] = [
            {"id": "c0", "channel": 0, "start_frame": 0, "count": 9},
            {"id": "c5", "channel": 5, "start_frame": 0, "count": 9},
        ]
        results = rf64.process(doc)["results"]
        self.assertEqual(results[0]["samples"], [0, 100])
        self.assertEqual(results[1]["samples"], [5, 105])
        self.assertEqual(len(results[0]["samples"]),
                         len(results[1]["samples"]))

    def test_tail_truncated_frame_beyond_declared_end_is_ignored(self):
        # Whole extra frame present past the declared chunk: still not read.
        doc = make_doc(channels=2, riff_size=0xFFFFFFFF, ds64_size=2, frames=3)
        doc["requests"] = [{"id": "t", "channel": "all",
                            "start_frame": 1, "count": 5}]
        self.assertEqual(rf64.process(doc)["results"][0]["samples"], [100, 101])


class FormatTests(unittest.TestCase):
    def test_24_bit_samples_in_32_bit_container_are_raw(self):
        # 0x7FFFFF / -0x800000 are full-scale signed 24-bit values.  In a
        # 32-bit container they pass through untouched: no width shift, no
        # scale conversion, and negative values stay negative.
        doc = {"channels": 2, "sample_rate": 48000, "bytes_per_sample": 4,
               "riff_size": 0xFFFFFFFF, "ds64_size": 2,
               "interleaved": [0x7FFFFF, -0x800000, 1, -1],
               "requests": [
                   {"id": "l", "channel": 0, "start_frame": 0, "count": 2},
                   {"id": "r", "channel": 1, "start_frame": 0, "count": 2},
               ]}
        results = rf64.process(doc)["results"]
        self.assertEqual(results[0]["samples"], [0x7FFFFF, 1])
        self.assertEqual(results[1]["samples"], [-0x800000, -1])

    def test_extra_unknown_blocks_do_not_shift_stride(self):
        # Unknown metadata chunks live in extra keys and must not alter the
        # frame/channel stride.
        doc = make_doc(channels=6, riff_size=0xFFFFFFFF, ds64_size=3, frames=3)
        doc["chunks"] = [{"id": "JUNK", "size": 3},
                         {"id": "bext", "size": 602}]
        doc["metadata"] = {"note": "recorder export"}
        doc["requests"] = [{"id": "ch4", "channel": 4,
                            "start_frame": 1, "count": 2}]
        self.assertEqual(rf64.process(doc)["results"][0]["samples"], [104, 204])

    def test_unknown_block_with_odd_size_keeps_later_channels_aligned(self):
        # Odd-sized chunk plus its pad byte must not be absorbed into the
        # data chunk: channel 5 of frame 0 is still the 6th sample.
        doc = make_doc(channels=6, riff_size=0xFFFFFFFF, ds64_size=2, frames=2)
        doc["chunks"] = [{"id": "odd!", "size": 5, "pad": 1}]
        doc["requests"] = [
            {"id": "first", "channel": 0, "start_frame": 0, "count": 1},
            {"id": "last", "channel": 5, "start_frame": 0, "count": 1},
        ]
        results = rf64.process(doc)["results"]
        self.assertEqual(results[0]["samples"], [0])
        self.assertEqual(results[1]["samples"], [5])

    def test_channel_sample_counts_are_whole_frames(self):
        doc = make_doc(channels=6, riff_size=0xFFFFFFFF, ds64_size=5, frames=7)
        doc["requests"] = [{"id": "all", "channel": "all",
                            "start_frame": 2, "count": 99},
                           {"id": "ch", "channel": 3,
                            "start_frame": 2, "count": 99}]
        results = rf64.process(doc)["results"]
        self.assertEqual(len(results[0]["samples"]), 3 * 6)
        self.assertEqual(len(results[1]["samples"]), 3)


class DeviceExportTests(unittest.TestCase):
    """End-to-end shape of a recorder export: RF64 + 24-in-32 + extra blocks."""

    def build(self):
        channels = 6
        frames = 4
        # 24-bit values stored in a 32-bit container, with a sample each
        # channel can be identified by, including negatives.
        data = []
        for f in range(frames):
            for c in range(channels):
                v = (f + 1) * 1000 + c
                data.append(-v if c % 2 else v)
        doc = {
            "channels": channels,
            "sample_rate": 48000,
            "bytes_per_sample": 4,          # container width
            "bits_per_sample": 24,          # valid width, informational here
            "riff_size": 0xFFFFFFFF,        # placeholder
            "ds64_size": frames,
            "chunks": [{"id": "JUNK", "size": 27},
                       {"id": "bext", "size": 603},
                       {"id": "LIST", "size": 26}],
            "interleaved": data,
            "requests": [
                {"id": "l", "channel": 0, "start_frame": 0, "count": frames},
                {"id": "c", "channel": 2, "start_frame": 1, "count": 2},
                {"id": "r", "channel": 5, "start_frame": 2, "count": 99},
                {"id": "raw", "channel": "all", "start_frame": 3, "count": 1},
            ],
        }
        return doc, data

    def test_each_channel_matches_expected_positions(self):
        doc, data = self.build()
        out = rf64.process(doc)
        self.assertEqual(out["frames_total"], 4)
        self.assertEqual(out["results"][0]["samples"], [1000, 2000, 3000, 4000])
        self.assertEqual(out["results"][1]["samples"], [2002, 3002])
        self.assertEqual(out["results"][2]["samples"], [-3005, -4005])
        # "all" is the untouched interleaved span for frame 3.
        self.assertEqual(out["results"][3]["samples"], data[3 * 6:4 * 6])


class CliTests(unittest.TestCase):
    def test_cli_round_trip(self):
        doc = make_doc(channels=2, riff_size=0xFFFFFFFF, ds64_size=4, frames=6)
        doc["requests"] = [
            {"id": "a", "channel": 0, "start_frame": 1, "count": 99},
            {"id": "b", "channel": "all", "start_frame": 2, "count": 1},
            {"id": "c", "channel": 1, "start_frame": 4, "count": 1},
        ]
        workdir = tempfile.mkdtemp(prefix="rf64-test-", dir=".")
        try:
            src = os.path.join(workdir, "in.json")
            dst = os.path.join(workdir, "out.json")
            with open(src, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            subprocess.run([sys.executable, os.path.abspath("rf64.py"), src, dst],
                           check=True)
            with open(dst, "r", encoding="utf-8") as fh:
                out = json.load(fh)
            self.assertEqual(out["frames_total"], 4)
            self.assertEqual(out["results"][0]["samples"], [100, 200, 300])
            self.assertEqual(out["results"][1]["samples"], [200, 201])
            self.assertEqual(out["results"][2], {"id": "c", "error": "eof"})
        finally:
            for name in os.listdir(workdir):
                os.unlink(os.path.join(workdir, name))
            os.rmdir(workdir)


if __name__ == "__main__":
    unittest.main()
