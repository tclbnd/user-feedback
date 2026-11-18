#!/usr/bin/env python3
"""Acceptance tests for the speaker-label remap.

Each source channel carries a distinct impulse value equal to its own source
index plus one, so an impulse at target position p identifies exactly which
source channel (sample identity) ended up there.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import remap


def impulse_segment(start, end, source_speakers):
    """One impulse per channel at a known offset, unique per source channel."""
    span = end - start
    if span < len(source_speakers):
        raise ValueError("segment too short to keep one impulse per channel")
    samples = []
    for index in range(len(source_speakers)):
        channel = [0] * span
        channel[index] = index + 1
        samples.append(channel)
    return {
        "start": start,
        "end": end,
        "source_speakers": list(source_speakers),
        "samples": samples,
    }


def impulse_positions(channel):
    return [i for i, value in enumerate(channel) if value != 0]


class SpeakerRemapTest(unittest.TestCase):
    def test_rear_and_lfe_are_not_swapped(self):
        # 5.1 delivered as FL FR FC LFE BL BR while playback expects BL BR order
        # swapped relative to the container. Ids, not label order, decide.
        target = ["FL", "FR", "FC", "LFE", "BL", "BR"]
        source = ["FL", "FR", "FC", "BR", "LFE", "BL"]
        data = {"target_speakers": target, "segments": [impulse_segment(0, 6, source)]}
        out = remap.remap(data)

        expected_source = [source.index(label) for label in target]
        self.assertEqual(expected_source, [0, 1, 2, 4, 5, 3])
        self.assertEqual(out["segment_report"], [{"status": "ok", "map": expected_source}])
        for position, source_index in enumerate(expected_source):
            channel = out["channels"][position]
            self.assertEqual(impulse_positions(channel), [source_index])
            self.assertEqual(channel[source_index], source_index + 1)

    def test_every_channel_is_independently_traced(self):
        target = ["FL", "FR", "FC", "LFE", "BL", "BR"]
        source = ["FC", "BL", "FL", "BR", "FR", "LFE"]
        data = {"target_speakers": target, "segments": [impulse_segment(0, 6, source)]}
        out = remap.remap(data)

        for position, label in enumerate(target):
            channel = out["channels"][position]
            source_index = source.index(label)
            self.assertEqual(impulse_positions(channel), [source_index], label)
            self.assertEqual(channel[source_index], source_index + 1, label)

    def test_duplicate_labels_mark_conflict_and_mute(self):
        target = ["FL", "FR", "FC"]
        source = ["FL", "FL", "FR"]
        data = {"target_speakers": target, "segments": [impulse_segment(0, 3, source)]}
        out = remap.remap(data)

        self.assertEqual(out["segment_report"], [{"status": "conflict", "map": [-1, -1, -1]}])
        for channel in out["channels"]:
            self.assertEqual(channel, [0, 0, 0])

    def test_duplicate_detected_even_if_other_labels_match(self):
        target = ["FL", "FR"]
        source = ["FR", "FR", "FL"]
        data = {"target_speakers": target, "segments": [impulse_segment(0, 3, source)]}
        out = remap.remap(data)

        self.assertEqual(out["segment_report"][0]["status"], "conflict")
        self.assertEqual(out["channels"], [[0, 0, 0], [0, 0, 0]])

    def test_unknown_layout_is_reported_not_guessed_alphabetically(self):
        # Labels that sort around the target ones must not be placed by order.
        target = ["FL", "FR", "FC", "LFE", "BL", "BR"]
        source = ["a", "b", "c"]
        data = {"target_speakers": target, "segments": [impulse_segment(0, 3, source)]}
        out = remap.remap(data)

        self.assertEqual(
            out["segment_report"],
            [{"status": "unknown", "map": [-1, -1, -1, -1, -1, -1]}],
        )
        for channel in out["channels"]:
            self.assertEqual(channel, [0, 0, 0])

    def test_unknown_sorts_before_but_is_still_not_matched(self):
        # "AA"/"ZZ" would sort onto FL/BR if alphabetical guessing remained.
        target = ["FL", "FR"]
        data = {"target_speakers": target, "segments": [impulse_segment(0, 2, ["AA", "ZZ"])]}
        out = remap.remap(data)

        self.assertEqual(out["segment_report"][0]["status"], "unknown")
        self.assertEqual(out["segment_report"][0]["map"], [-1, -1])

    def test_missing_speaker_fills_target_position_with_silence(self):
        target = ["FL", "FR", "FC"]
        source = ["FL", "FC"]  # FR absent
        data = {"target_speakers": target, "segments": [impulse_segment(0, 2, source)]}
        out = remap.remap(data)

        self.assertEqual(out["segment_report"], [{"status": "ok", "map": [0, -1, 1]}])
        self.assertEqual(impulse_positions(out["channels"][1]), [])
        self.assertEqual(out["channels"][1], [0, 0])

    def test_target_order_change_moves_output_positions(self):
        source = ["FL", "FR", "FC"]
        segment = impulse_segment(0, 3, source)
        flat = remap.remap({"target_speakers": ["FL", "FR", "FC"], "segments": [segment]})
        swapped = remap.remap({"target_speakers": ["FC", "FR", "FL"], "segments": [segment]})

        self.assertEqual(flat["segment_report"], [{"status": "ok", "map": [0, 1, 2]}])
        self.assertEqual(swapped["segment_report"], [{"status": "ok", "map": [2, 1, 0]}])
        self.assertEqual(flat["channels"], list(reversed(swapped["channels"])))
        self.assertEqual(
            [impulse_positions(channel) for channel in swapped["channels"]],
            [[2], [1], [0]],
        )

    def test_layout_switch_between_segments(self):
        # Segment A: FL FR FC. Segment B: same program but re-tagged as a
        # re-muxed 5.1 would deliver (BR/BL ahead of the front channels).
        target = ["FL", "FR", "FC", "LFE", "BL", "BR"]
        seg_a = impulse_segment(0, 6, ["FL", "FR", "FC", "LFE", "BL", "BR"])
        seg_b = impulse_segment(6, 12, ["BL", "BR", "FL", "FR", "FC", "LFE"])
        out = remap.remap({"target_speakers": target, "segments": [seg_a, seg_b]})

        self.assertEqual(
            [r["status"] for r in out["segment_report"]], ["ok", "ok"]
        )
        self.assertEqual(out["segment_report"][0]["map"], [0, 1, 2, 3, 4, 5])
        self.assertEqual(out["segment_report"][1]["map"], [2, 3, 4, 5, 0, 1])
        self.assertTrue(all(len(channel) == 12 for channel in out["channels"]))
        for position, label in enumerate(target):
            first = out["channels"][position][:6]
            second = out["channels"][position][6:]
            # Sample identity: an impulse is only accepted from the channel
            # that actually carries this speaker's id in that segment.
            self.assertEqual(impulse_positions(first), [seg_a["source_speakers"].index(label)], label)
            self.assertEqual(impulse_positions(second), [seg_b["source_speakers"].index(label)], label)

    def test_unknown_segment_between_ok_segments_stays_aligned(self):
        target = ["FL", "FR"]
        good = impulse_segment(0, 2, ["FL", "FR"])
        bad = impulse_segment(2, 5, ["x", "y"])
        tail = impulse_segment(5, 7, ["FR", "FL"])
        out = remap.remap({"target_speakers": target, "segments": [good, bad, tail]})

        self.assertEqual([r["status"] for r in out["segment_report"]], ["ok", "unknown", "ok"])
        self.assertEqual(out["segment_report"][2]["map"], [1, 0])
        for channel in out["channels"]:
            self.assertEqual(len(channel), 7)
            self.assertEqual(channel[2:5], [0, 0, 0])
        # tail source ["FR", "FL"]: FL comes from source 1, FR from source 0.
        self.assertEqual(out["channels"][0][5:], [0, 2])
        self.assertEqual(out["channels"][1][5:], [1, 0])

    def test_extra_source_channels_are_ignored(self):
        target = ["FL", "FR"]
        source = ["FL", "FR", "NOISE", "AUX"]
        out = remap.remap({"target_speakers": target, "segments": [impulse_segment(0, 4, source)]})

        self.assertEqual(out["segment_report"], [{"status": "ok", "map": [0, 1]}])
        self.assertEqual(out["channels"], [[1, 0, 0, 0], [0, 2, 0, 0]])

    def test_missing_channel_reason_differs_from_conflict_and_unknown(self):
        target = ["FL", "FR", "FC"]
        absent = remap.remap(
            {"target_speakers": target, "segments": [impulse_segment(0, 2, ["FL", "FC"])]}
        )
        duplicate = remap.remap(
            {"target_speakers": target, "segments": [impulse_segment(0, 3, ["FL", "FL", "FC"])]}
        )
        unknown = remap.remap(
            {"target_speakers": target, "segments": [impulse_segment(0, 2, ["x", "y"])]}
        )

        # Absent speaker: segment is still usable, only that slot is silent.
        self.assertEqual(absent["segment_report"], [{"status": "ok", "map": [0, -1, 1]}])
        self.assertEqual(absent["channels"][1], [0, 0])
        self.assertEqual(absent["channels"][0], [1, 0])
        # Ambiguous duplicate: whole segment is muted, all slots -1.
        self.assertEqual(duplicate["segment_report"], [{"status": "conflict", "map": [-1, -1, -1]}])
        self.assertEqual([sum(channel) for channel in duplicate["channels"]], [0, 0, 0])
        # Unknown layout: reported, all slots -1, nothing guessed.
        self.assertEqual(unknown["segment_report"], [{"status": "unknown", "map": [-1, -1, -1]}])

    def test_fixtures(self):
        expected = {
            "swapped_51.json": ([{"status": "ok", "map": [0, 1, 2, 4, 5, 3]}],
                                [[1, 0, 0, 0, 0, 0], [0, 2, 0, 0, 0, 0], [0, 0, 3, 0, 0, 0],
                                 [0, 0, 0, 0, 5, 0], [0, 0, 0, 0, 0, 6], [0, 0, 0, 4, 0, 0]]),
            "remux_side_rears.json": ([{"status": "ok", "map": [0, 1, 2, 4, 5, 3]}],
                                      [[1, 0, 0, 0, 0, 0], [0, 2, 0, 0, 0, 0], [0, 0, 3, 0, 0, 0],
                                       [0, 0, 0, 0, 5, 0], [0, 0, 0, 0, 0, 6], [0, 0, 0, 4, 0, 0]]),
            "missing_channel_extra_label.json": (
                [{"status": "ok", "map": [0, 1, 2, 3, -1, -1]}],
                [[1, 0, 0, 0, 0], [0, 2, 0, 0, 0], [0, 0, 3, 0, 0],
                 [0, 0, 0, 4, 0], [0, 0, 0, 0, 0], [0, 0, 0, 0, 0]],
            ),
            "unknown_layout.json": ([{"status": "unknown", "map": [-1] * 6}], [[0, 0, 0]] * 6),
            "unknown_two_channel.json": (
                [{"status": "unknown", "map": [-1, -1]}],
                [[0, 0, 0], [0, 0, 0]],
            ),
            "conflict_same_frame.json": ([{"status": "conflict", "map": [-1] * 6}], [[0, 0, 0, 0]] * 6),
        }
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
        for name, (report, channels) in expected.items():
            with self.subTest(fixture=name):
                with open(os.path.join(root, name), "r", encoding="utf-8") as handle:
                    out = remap.remap(json.load(handle))
                self.assertEqual(out["segment_report"], report)
                self.assertEqual(out["channels"], channels)

        with open(os.path.join(root, "switch_two_segments.json"), "r", encoding="utf-8") as handle:
            switched = remap.remap(json.load(handle))
        self.assertEqual(
            switched["segment_report"],
            [{"status": "ok", "map": [0, 1, 2, 3, 4, 5]},
             {"status": "ok", "map": [2, 3, 4, 5, 0, 1]}],
        )
        # Switch point keeps sample identity: second-half impulses at 6..11.
        for position, source_index in enumerate([2, 3, 4, 5, 0, 1]):
            second = switched["channels"][position][6:]
            self.assertEqual(impulse_positions(second), [source_index], position)

    def test_short_source_channel_keeps_timeline_aligned(self):
        target = ["FL", "FR"]
        segment = {
            "start": 0,
            "end": 4,
            "source_speakers": ["FL", "FR"],
            "samples": [[1, 2], [3, 4]],  # channel shorter than the interval
        }
        out = remap.remap({"target_speakers": target, "segments": [segment]})

        self.assertEqual(out["channels"], [[1, 2, 0, 0], [3, 4, 0, 0]])

    def test_gap_between_segments_is_silent_at_target_width(self):
        target = ["FL", "FR"]
        first = impulse_segment(0, 2, ["FL", "FR"])
        later = impulse_segment(5, 7, ["FL", "FR"])
        out = remap.remap({"target_speakers": target, "segments": [first, later]})

        self.assertTrue(all(len(channel) == 7 for channel in out["channels"]))
        self.assertEqual(out["channels"][0], [1, 0, 0, 0, 0, 1, 0])
        self.assertEqual(out["channels"][1], [0, 2, 0, 0, 0, 0, 2])

    def test_cli_round_trip(self):
        data = {
            "target_speakers": ["FL", "FR", "FC", "LFE", "BL", "BR"],
            "segments": [impulse_segment(0, 6, ["FL", "FR", "FC", "BR", "LFE", "BL"])],
        }
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "in.json")
            dst = os.path.join(tmp, "out.json")
            with open(src, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            subprocess.run([sys.executable, "remap.py", src, dst], check=True)
            with open(dst, "r", encoding="utf-8") as handle:
                out = json.load(handle)

        self.assertEqual(out["segment_report"], [{"status": "ok", "map": [0, 1, 2, 4, 5, 3]}])
        self.assertEqual(
            out["channels"],
            [[1, 0, 0, 0, 0, 0], [0, 2, 0, 0, 0, 0], [0, 0, 3, 0, 0, 0],
             [0, 0, 0, 0, 5, 0], [0, 0, 0, 0, 0, 6], [0, 0, 0, 4, 0, 0]],
        )


if __name__ == "__main__":
    unittest.main()
