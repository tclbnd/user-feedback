#!/usr/bin/env python3
"""Regression tests for the frame-accurate black/freeze detector."""

import json
import os
import random
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import detect  # noqa: E402


def frame(pts, luma, diff):
    return {"pts": pts, "luma": luma, "diff": diff}


def windows(**overrides):
    base = {
        "black_luma": 20.0,
        "freeze_diff": 2.0,
        "min_black_ms": 160,
        "min_freeze_ms": 160,
        "gap_ms": 60,
    }
    base.update(overrides)
    return base


def run(frames, frame_ms=40, **overrides):
    return detect.detect(
        {"frames": frames, "frame_ms": frame_ms, "windows": windows(**overrides)}
    )


def simple(events):
    return [
        (e["kind"], e["start"], e["end"], e["frames"], e["reason"])
        for e in events
    ]


def frame_gold(frames, window, frame_ms, marked=()):
    """Independent per-frame oracle.

    Classifies every kept frame, walks the timeline to build maximal runs,
    merges same-kind runs across unflagged gaps shorter than ``gap_ms`` and
    applies the minimum duration.  Returns (kind, start, end, frames, reason).

    This is deliberately written as a separate, straightforward transcription
    of the documented rules so a bug in the production state machine shows up
    as a mismatch instead of being mirrored.
    """
    kept = []
    for index, item in enumerate(frames):
        if kept and item["pts"] < kept[-1]["pts"]:
            continue
        kept.append(item)
    if not kept:
        return []

    ends = [kept[i + 1]["pts"] if i + 1 < len(kept) else
            kept[i]["pts"] + frame_ms for i in range(len(kept))]

    def flags(i):
        item = kept[i]
        if any(item.get(key) is True for key in
               ("marked_black", "intentional_black", "reserved_black",
                "expected_black")):
            return []
        if any(start <= item["pts"] < end for start, end in marked):
            return []
        result = []
        if item["luma"] < window["black_luma"]:
            result.append("black")
        frozen = i != 0 and (
            item["diff"] == 0 or item["diff"] < window["freeze_diff"]
        )
        if frozen:
            result.append("freeze")
        return result

    runs = []
    current = None
    for i in range(len(kept)):
        tags = flags(i)
        if not tags:
            current = None
            continue
        both = len(tags) == 2
        if current is not None and (both or tags[0] == current["kind"]):
            current["last"] = i
            current["both"] = current["both"] or both
            continue
        current = {
            "kind": tags[0],
            "first": i,
            "last": i,
            "both": both,
        }
        runs.append(current)

    merged = []
    for run in runs:
        start = kept[run["first"]]["pts"]
        end = ends[run["last"]]
        if merged and merged[-1]["kind"] == run["kind"] and \
                start - merged[-1]["end"] < window["gap_ms"]:
            merged[-1]["end"] = end
            merged[-1]["last"] = run["last"]
            merged[-1]["both"] = merged[-1]["both"] or run["both"]
        else:
            merged.append(dict(run, start=start, end=end))

    gold = []
    for run in merged:
        # A run that occupies no time on the timeline (all its frames share a
        # pts with the next frame) never reached the screen, so it cannot be
        # an alarm.
        if run["end"] <= run["start"]:
            continue
        minimum = (window["min_black_ms"] if run["kind"] == "black"
                   else window["min_freeze_ms"])
        if run["end"] - run["start"] < minimum:
            continue
        gold.append((
            run["kind"],
            run["start"],
            run["end"],
            run["last"] - run["first"] + 1,
            "both" if run["both"] else run["kind"],
        ))
    return gold


def _marked_ranges_from(data):
    ranges = []
    for key in ("marked_black", "intentional_black", "reserved_black",
                "expected_black", "black_ranges", "slates"):
        for item in data.get(key) or ():
            if isinstance(item, dict):
                ranges.append((item["start"], item["end"]))
            else:
                ranges.append((item[0], item[1]))
    return ranges


def assert_matches_frame_gold(case, frames, data, result):
    """Check events, playable coverage and skip reporting against the oracle."""
    gold = frame_gold(
        frames, data["windows"], data["frame_ms"], _marked_ranges_from(data)
    )
    case.assertEqual(simple(result["events"]), gold)

    kept = []
    for i, item in enumerate(frames):
        if kept and item["pts"] < kept[-1]["pts"]:
            continue
        kept.append(item)
    case.assertEqual(len(kept) + len(result["skipped"]), len(frames))

    # Every kept frame belongs to exactly one reported interval.
    covered = [False] * len(kept)
    for span in list(result["events"]) + list(result["playable"]):
        for i, item in enumerate(kept):
            if span["start"] <= item["pts"] < span["end"]:
                case.assertFalse(
                    covered[i],
                    "frame %d covered by overlapping intervals" % i,
                )
                covered[i] = True
    case.assertTrue(all(covered), "kept frames left uncovered")


class BasicTests(unittest.TestCase):
    def test_freeze_run(self):
        frames = [frame(i * 40, 100, 20) for i in range(5)]
        frames += [frame((5 + i) * 40, 100, 0) for i in range(4)]
        frames += [frame(9 * 40, 100, 20)]
        out = run(frames)
        self.assertEqual(
            simple(out["events"]), [("freeze", 200, 360, 4, "freeze")]
        )
        self.assertEqual(
            [(p["start"], p["end"], p["frames"]) for p in out["playable"]],
            [(0, 200, 5), (360, 400, 1)],
        )
        self.assertEqual(out["skipped"], [])

    def test_first_frame_never_freeze(self):
        frames = [frame(0, 100, 0), frame(40, 100, 20), frame(80, 100, 20)]
        self.assertEqual(run(frames)["events"], [])

    def test_first_frame_black_diff_zero_is_black_only(self):
        # First frame has diff == 0 but counts toward black only.
        frames = [frame(0, 10, 0)] + [
            frame(i * 40, 10, 20) for i in range(1, 4)
        ]
        out = run(frames, min_black_ms=160, min_freeze_ms=1)
        self.assertEqual(simple(out["events"]), [("black", 0, 160, 4, "black")])

    def test_black_run_containing_both_frames_reports_both(self):
        frames = [frame(0, 100, 20)] + [
            frame(i * 40, 10, 0) for i in range(1, 5)
        ]
        out = run(frames, min_black_ms=160)
        self.assertEqual(
            simple(out["events"]), [("black", 40, 200, 4, "both")]
        )

    def test_strict_threshold_equality(self):
        # luma == black_luma and diff == freeze_diff are not flagged.
        frames = [frame(0, 100, 20), frame(40, 20, 20), frame(80, 100, 2)]
        self.assertEqual(run(frames)["events"], [])

    def test_diff_zero_is_frozen(self):
        # freeze_diff of zero still treats exact zeros as frozen frames.
        frames = [frame(0, 100, 20)] + [frame(i * 40, 100, 0) for i in range(1, 6)]
        out = run(frames, freeze_diff=0)
        self.assertEqual(simple(out["events"]), [("freeze", 40, 240, 5, "freeze")])


class MergeAndBoundaryTests(unittest.TestCase):
    def test_single_frame_gap_merges(self):
        frames = [frame(i * 40, 100, 20) for i in range(5)]
        # freeze; spike; freeze; freeze
        frames += [frame(200, 100, 0), frame(240, 100, 9),
                   frame(280, 100, 0), frame(320, 100, 0)]
        frames += [frame(360, 100, 20), frame(400, 100, 20)]
        out = run(frames, gap_ms=60)
        # The absorbed one-frame gap lies inside the reported interval, so it
        # is counted in the span: frames 200/240/280/320.
        self.assertEqual(
            simple(out["events"]), [("freeze", 200, 360, 4, "freeze")]
        )
        self.assertEqual(len(out["events"]), 1)

    def test_gap_equal_to_window_does_not_merge(self):
        frames = [frame(i * 40, 100, 20) for i in range(5)]
        frames += [frame(200, 100, 0), frame(240, 100, 9),
                   frame(280, 100, 0), frame(320, 100, 0)]
        frames += [frame(360, 100, 20), frame(400, 100, 20)]
        out = run(frames, gap_ms=40)
        # 40 ms gap is not "less than" 40 ms, and both pieces are too short.
        self.assertEqual(out["events"], [])

    def test_long_freeze_with_many_blips_is_one_event(self):
        frames = [frame(0, 100, 20)]
        pts = 40
        for i in range(100):
            frames.append(frame(pts, 100, 0))
            pts += 40
            if i % 7 == 6:
                frames.append(frame(pts, 100, 9))
                pts += 40
        frames.append(frame(pts, 100, 20))
        out = run(frames, min_freeze_ms=1000, gap_ms=60)
        self.assertEqual(len(out["events"]), 1)
        event = out["events"][0]
        self.assertEqual(event["kind"], "freeze")
        self.assertEqual(event["start"], 40)
        self.assertGreaterEqual(event["end"] - event["start"], 1000)

    def test_min_duration_equality(self):
        frames = [frame(0, 100, 20)]
        frames += [frame(i * 40, 100, 0) for i in range(1, 4)]  # 3 * 40 = 120
        frames += [frame(4 * 40, 100, 20)]
        self.assertEqual(run(frames, min_freeze_ms=120)["events"][0]["kind"],
                         "freeze")
        self.assertEqual(run(frames, min_freeze_ms=121)["events"], [])

    def test_separate_kinds_are_not_merged(self):
        frames = [frame(0, 100, 20)]
        frames += [frame((i + 1) * 40, 100, 0) for i in range(4)]   # freeze
        frames += [frame((i + 5) * 40, 10, 20) for i in range(4)]   # black
        frames += [frame((i + 9) * 40, 100, 0) for i in range(4)]   # freeze
        frames += [frame(13 * 40, 100, 20)]
        out = run(frames, gap_ms=200)
        self.assertEqual(
            simple(out["events"]),
            [("freeze", 40, 200, 4, "freeze"),
             ("black", 200, 360, 4, "black"),
             ("freeze", 360, 520, 4, "freeze")],
        )


class BothTests(unittest.TestCase):
    def test_both_extends_black_run(self):
        frames = [
            frame(0, 100, 20),
            frame(40, 10, 20),   # black
            frame(80, 10, 0),    # both -> joins black
            frame(120, 10, 20),  # black
            frame(160, 100, 20),
        ]
        out = run(frames, min_black_ms=120)
        self.assertEqual(
            simple(out["events"]), [("black", 40, 160, 3, "both")]
        )

    def test_both_extends_freeze_run(self):
        frames = [
            frame(0, 100, 20),
            frame(40, 100, 0),  # freeze
            frame(80, 10, 0),   # both -> joins freeze
            frame(120, 100, 0),  # freeze
            frame(160, 100, 20),
        ]
        out = run(frames, min_freeze_ms=120)
        self.assertEqual(
            simple(out["events"]), [("freeze", 40, 160, 3, "both")]
        )

    def test_both_opening_run_defaults_to_black(self):
        frames = [
            frame(0, 10, 0),    # first frame: black, diff ignored
            frame(40, 10, 20),
            frame(80, 100, 20),
        ]
        out = run(frames, min_black_ms=80)
        self.assertEqual(
            simple(out["events"]), [("black", 0, 80, 2, "black")]
        )

    def test_dynamic_black_background_is_one_black_event(self):
        diffs = [20, 0, 9, 0, 15, 0]
        frames = [frame(0, 100, 20)]
        for i, diff in enumerate(diffs):
            frames.append(frame((i + 1) * 40, 10, diff))
        frames.append(frame((len(diffs) + 1) * 40, 100, 20))
        out = run(frames, min_black_ms=160, min_freeze_ms=80, gap_ms=60)
        self.assertEqual(len(out["events"]), 1)
        event = out["events"][0]
        self.assertEqual((event["kind"], event["start"], event["end"]),
                         ("black", 40, 280))
        # Frozen black frames are reported as both, never as a second alarm.
        self.assertEqual(event["reason"], "both")
        self.assertFalse(any(e["kind"] == "freeze" for e in out["events"]))


class TimelineTests(unittest.TestCase):
    def test_variable_frame_rate_uses_real_pts(self):
        frames = [
            frame(0, 100, 20),
            frame(30, 100, 0),
            frame(100, 100, 0),
            frame(130, 100, 0),
            frame(200, 100, 20),
        ]
        out = run(frames, frame_ms=30, min_freeze_ms=150)
        self.assertEqual(
            simple(out["events"]), [("freeze", 30, 200, 3, "freeze")]
        )

    def test_dropped_frames_extend_hold_interval(self):
        frames = [
            frame(0, 100, 20),
            frame(40, 100, 0),
            frame(400, 100, 0),  # ten nominal frames were dropped
            frame(440, 100, 20),
        ]
        out = run(frames, frame_ms=40, min_freeze_ms=300)
        self.assertEqual(
            simple(out["events"]), [("freeze", 40, 440, 2, "freeze")]
        )

    def test_skipped_backwards_pts_reported_by_index(self):
        frames = [
            frame(0, 100, 20),
            frame(40, 100, 0),
            frame(20, 10, 0),   # backwards -> skipped
            frame(80, 100, 0),
            frame(120, 100, 0),
            frame(160, 100, 20),
        ]
        out = run(frames, frame_ms=40, min_freeze_ms=120)
        self.assertEqual(out["skipped"], [2])
        self.assertEqual(
            simple(out["events"]), [("freeze", 40, 160, 3, "freeze")]
        )
        # The skipped frame is not part of any playable range.
        self.assertEqual(
            [(p["start"], p["end"], p["frames"]) for p in out["playable"]],
            [(0, 40, 1), (160, 200, 1)],
        )

    def test_equal_pts_allowed(self):
        frames = [
            frame(0, 100, 20),
            frame(40, 100, 0),
            frame(40, 100, 0),
            frame(80, 100, 0),
            frame(120, 100, 20),
        ]
        out = run(frames, min_freeze_ms=40)
        self.assertEqual(out["skipped"], [])
        self.assertEqual(
            simple(out["events"]), [("freeze", 40, 120, 3, "freeze")]
        )

    def test_vfr_playable_ranges(self):
        frames = [
            frame(0, 100, 20),
            frame(10, 100, 20),
            frame(15, 100, 0),
            frame(100, 100, 0),
            frame(105, 100, 0),
            frame(300, 100, 20),
        ]
        out = run(frames, frame_ms=10, min_freeze_ms=90, gap_ms=5)
        self.assertEqual(
            simple(out["events"]), [("freeze", 15, 300, 3, "freeze")]
        )
        self.assertEqual(
            [(p["start"], p["end"], p["frames"]) for p in out["playable"]],
            [(0, 15, 2), (300, 310, 1)],
        )


class CliAndChunkTests(unittest.TestCase):
    def test_cli_roundtrip(self):
        data = {
            "frames": [frame(0, 100, 20)] + [frame(i * 40, 100, 0)
                                              for i in range(1, 5)],
            "frame_ms": 40,
            "windows": windows(),
        }
        with tempfile.TemporaryDirectory() as tmp:
            inp = os.path.join(tmp, "in.json")
            outp = os.path.join(tmp, "out.json")
            with open(inp, "w") as handle:
                json.dump(data, handle)
            subprocess.run(
                [sys.executable, os.path.join(ROOT, "detect.py"), inp, outp],
                check=True,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            )
            with open(outp) as handle:
                result = json.load(handle)
        self.assertEqual(result["events"][0]["kind"], "freeze")

    def _document(self):
        frames = [frame(0, 100, 20)]
        for i in range(1, 30):
            # a long freeze with periodic diff spikes, plus a black stretch
            if 10 <= i < 16:
                frames.append(frame(i * 40, 10, 0 if i % 3 else 5))
            else:
                frames.append(frame(i * 40, 100, 0 if i % 4 else 9))
        frames.append(frame(30 * 40, 100, 20))
        return {"frames": frames, "frame_ms": 40, "windows": windows()}

    def test_incremental_feed_matches_whole_document(self):
        data = self._document()
        expected = detect.detect(data)
        self.assertTrue(expected["events"])

        frames = data["frames"]
        for size in (1, 2, 3, 5, 7, 100):
            detector = detect.Detector(data["windows"], data["frame_ms"])
            for start in range(0, len(frames), size):
                detector.feed(frames[start:start + size])
            # An open run spanning the last block must still be emitted once.
            self.assertEqual(detector.close(), expected)

    def test_run_spanning_chunk_boundary_is_one_event(self):
        frames = [frame(0, 100, 20)]
        for i in range(1, 30):
            frames.append(frame(i * 40, 100, 0 if i % 4 else 9))
        frames.append(frame(30 * 40, 100, 20))
        data = {"frames": frames, "frame_ms": 40, "windows": windows()}
        expected = detect.detect(data)
        freeze_events = [e for e in expected["events"] if e["kind"] == "freeze"]
        self.assertEqual(len(freeze_events), 1)
        run_start = freeze_events[0]["start"]

        # Split exactly in the middle of the freeze run.
        split = (run_start // 40) + 3
        result = (
            detect.Detector(data["windows"], data["frame_ms"])
            .feed(frames[:split])
            .feed(frames[split:])
            .close()
        )
        self.assertEqual(result["events"], expected["events"])
        self.assertEqual(
            len([e for e in result["events"] if e["start"] == run_start]), 1
        )

    def test_skipped_at_chunk_boundary_reported_once(self):
        frames = [
            frame(0, 100, 20),
            frame(40, 100, 0),
            frame(20, 100, 0),   # skipped
            frame(80, 100, 0),
            frame(120, 100, 20),
        ]
        data = {"frames": frames, "frame_ms": 40, "windows": windows()}
        detector = detect.Detector(data["windows"], data["frame_ms"])
        detector.feed(frames[:2]).feed(frames[2:3]).feed(frames[3:])
        result = detector.close()
        self.assertEqual(result["skipped"], [2])
        self.assertEqual(result, detect.detect(data))

    def test_close_twice_and_push_after_close_are_errors(self):
        detector = detect.Detector(windows(), 40)
        detector.close()
        with self.assertRaises(RuntimeError):
            detector.close()
        with self.assertRaises(RuntimeError):
            detector.push(frame(0, 100, 20))


class PropertyTests(unittest.TestCase):
    """Invariants that must hold for arbitrary generated timelines."""

    def _random_document(self, rng):
        frames = []
        pts = 0
        first = True
        for _ in range(rng.randint(1, 60)):
            step = rng.choice([10, 20, 33, 40, 40, 80])
            pts += step
            luma = rng.choice([5.0, 10.0, 19.9, 20.0, 20.1, 80.0, 200.0])
            diff = rng.choice([0.0, 1.0, 1.999, 2.0, 2.001, 8.0, 40.0])
            if first:
                diff = 0.0
                first = False
            frames.append(frame(pts, luma, diff))
        return {
            "frames": frames,
            "frame_ms": 40,
            "windows": windows(gap_ms=rng.choice([0, 40, 60, 100])),
        }

    def test_events_never_overlap_and_are_sorted(self):
        rng = random.Random(1234)
        for _ in range(200):
            data = self._random_document(rng)
            events = detect.detect(data)["events"]
            for earlier, later in zip(events, events[1:]):
                self.assertLessEqual(earlier["end"], later["start"])
            for event in events:
                self.assertLess(event["start"], event["end"])
                self.assertGreater(event["frames"], 0)

    def test_playable_is_exact_complement_of_events(self):
        rng = random.Random(99)
        for _ in range(200):
            data = self._random_document(rng)
            result = detect.detect(data)
            frames = data["frames"]

            def positions(start, end):
                return {
                    i for i, item in enumerate(frames)
                    if start <= item["pts"] < end
                }

            covered = set()
            for event in result["events"]:
                covered |= positions(event["start"], event["end"])

            playable_positions = set()
            for span in result["playable"]:
                playable_positions |= positions(span["start"], span["end"])

            self.assertEqual(covered & playable_positions, set())
            self.assertEqual(covered | playable_positions, set(range(len(frames))))

            for left, right in zip(result["playable"], result["playable"][1:]):
                self.assertLessEqual(left["end"], right["start"])

    def test_chunked_result_is_identical(self):
        rng = random.Random(7)
        for _ in range(100):
            data = self._random_document(rng)
            expected = detect.detect(data)
            frames = data["frames"]
            size = rng.randint(1, 6)
            detector = detect.Detector(data["windows"], data["frame_ms"])
            for start in range(0, len(frames), size):
                detector.feed(frames[start:start + size])
            self.assertEqual(detector.close(), expected)

    def test_events_respect_kind_minimums(self):
        rng = random.Random(555)
        for _ in range(200):
            data = self._random_document(rng)
            for event in detect.detect(data)["events"]:
                minimum = (
                    data["windows"]["min_black_ms"]
                    if event["kind"] == "black"
                    else data["windows"]["min_freeze_ms"]
                )
                self.assertGreaterEqual(event["end"] - event["start"], minimum)

    def test_random_timelines_match_frame_gold(self):
        rng = random.Random(20241117)
        for _ in range(300):
            data = self._random_document(rng)
            data["frames"] = [
                frame(0, 100, 20),
            ] + data["frames"]
            data["frame_ms"] = 40
            result = detect.detect(data)
            assert_matches_frame_gold(self, data["frames"], data, result)


class MarkedBlackAndContinuationTests(unittest.TestCase):
    def test_per_frame_marker_suppresses_black_alarm(self):
        frames = [frame(0, 100, 20)]
        frames += [frame(i * 40, 10, 0) for i in range(1, 5)]
        for item in frames[1:]:
            item["marked_black"] = True
        frames += [frame(5 * 40, 100, 20)]
        out = run(frames, min_black_ms=80)
        self.assertEqual(out["events"], [])
        self.assertEqual(
            [(p["start"], p["end"]) for p in out["playable"]], [(0, 240)]
        )

    def test_top_level_ranges_suppress_reserved_black(self):
        frames = [frame(0, 100, 20)]
        frames += [frame(i * 40, 10, 0) for i in range(1, 7)]
        frames += [frame(i * 40, 100, 20) for i in range(7, 10)]
        data = {
            "frames": frames,
            "frame_ms": 40,
            "windows": windows(min_black_ms=80),
            "marked_black": [{"start": 40, "end": 280}],
        }
        out = detect.detect(data)
        self.assertEqual(out["events"], [])
        self.assertEqual(out["playable"][0]["start"], 0)
        self.assertEqual(out["playable"][0]["end"], 400)

    def test_marker_only_covers_its_own_frames(self):
        frames = [frame(0, 100, 20)]
        frames += [frame(i * 40, 10, 0) for i in range(1, 7)]
        for item in frames[2:4]:
            item["marked_black"] = True
        frames += [frame(7 * 40, 100, 20)]
        out = run(frames, min_black_ms=80, gap_ms=40)
        # Marked frames split the black run.  The unmarked piece before the
        # marker (one frame) is too short.  The unmarked piece after it is
        # reported; the marked frames themselves are not part of the alarm.
        self.assertEqual(
            simple(out["events"]), [("black", 160, 280, 3, "both")]
        )
        self.assertEqual(
            [(p["start"], p["end"]) for p in out["playable"]],
            [(0, 160), (280, 320)],
        )

    def test_marked_gap_can_prevent_merge(self):
        # Two freeze runs separated by marked black frames and by a gap that
        # is >= gap_ms must stay separate: marked frames are unflagged, but
        # the gap is too long to merge.
        frames = [frame(0, 100, 20)]
        frames += [frame((i + 1) * 40, 100, 0) for i in range(3)]
        frames += [frame(4 * 40, 10, 0), frame(5 * 40, 10, 0)]
        for item in frames[-2:]:
            item["marked_black"] = True
        frames += [frame((i + 6) * 40, 100, 0) for i in range(3)]
        frames += [frame(9 * 40, 100, 20)]
        out = run(frames, gap_ms=80, min_freeze_ms=80)
        self.assertEqual(
            simple(out["events"]),
            [("freeze", 40, 160, 3, "freeze"),
             ("freeze", 240, 360, 3, "freeze")],
        )

    def test_marked_gap_within_window_merges_per_documented_rule(self):
        # Marked frames are unflagged frames, so a gap shorter than gap_ms
        # merges exactly like any other unflagged gap.
        frames = [frame(0, 100, 20)]
        frames += [frame((i + 1) * 40, 100, 0) for i in range(3)]
        frames += [frame(4 * 40, 10, 0), frame(5 * 40, 10, 0)]
        for item in frames[-2:]:
            item["marked_black"] = True
        frames += [frame((i + 6) * 40, 100, 0) for i in range(3)]
        frames += [frame(9 * 40, 100, 20)]
        out = run(frames, gap_ms=200, min_freeze_ms=80)
        self.assertEqual(
            simple(out["events"]), [("freeze", 40, 360, 8, "freeze")]
        )

    def test_backwards_pts_inside_run_does_not_create_second_event(self):
        # A decode-order glitch inside one continuous black stretch is
        # reported in `skipped`; it must not split the alert or add a second
        # one for the same stretch.
        frames = [
            frame(0, 100, 20),
            frame(40, 10, 20),
            frame(80, 10, 20),
            frame(60, 10, 20),    # skipped: pts moved backwards
            frame(120, 10, 20),
            frame(160, 10, 20),
            frame(200, 100, 20),
        ]
        out = run(frames, gap_ms=500, min_black_ms=80)
        self.assertEqual(out["skipped"], [3])
        self.assertEqual(len(out["events"]), 1)
        self.assertEqual(out["events"][0]["start"], 40)
        self.assertEqual(out["events"][0]["end"], 200)
        self.assertEqual(out["events"][0]["frames"], 4)


class AcceptanceScenarioTests(unittest.TestCase):
    """End-to-end checks for the requested acceptance material.

    Each scenario goes through the CLI so the frame classification, event
    merging, threshold boundaries and playable ranges are verified together
    against the independent frame-level oracle.
    """

    def _run_cli(self, data, chunk=None):
        with tempfile.TemporaryDirectory() as tmp:
            inp = os.path.join(tmp, "in.json")
            outp = os.path.join(tmp, "out.json")
            with open(inp, "w") as handle:
                json.dump(data, handle)
            command = [sys.executable, os.path.join(ROOT, "detect.py")]
            if chunk is not None:
                command += ["--chunk", str(chunk)]
            command += [inp, outp]
            subprocess.run(
                command,
                check=True,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            )
            with open(outp) as handle:
                return json.load(handle)

    def test_variable_frame_rate_clip(self):
        pts = [0, 10, 10, 25, 60, 61, 100, 140, 200, 260, 261, 300]
        luma = [100] * 12
        diff = [20, 20, 20, 0, 0, 0, 0, 0, 9, 20, 20, 20]
        frames = [frame(p, l, d) for p, l, d in zip(pts, luma, diff)]
        data = {"frames": frames, "frame_ms": 40, "windows": windows(
            min_freeze_ms=170, gap_ms=45)}
        result = self._run_cli(data)
        assert_matches_frame_gold(self, frames, data, result)
        # Frames at 25/60/61/100/140 are frozen and held until the frame at
        # 200 (diff 9).  Real pts spacing gives 175 ms for the run, which
        # clears the 170 ms window; a 5 * frame_ms estimate would be wrong
        # on the other side of that boundary.
        self.assertEqual(
            simple(result["events"]), [("freeze", 25, 200, 5, "freeze")]
        )
        self.assertEqual(
            [(p["start"], p["end"]) for p in result["playable"]],
            [(0, 25), (200, 340)],
        )

    def test_long_static_subtitle_is_not_freeze(self):
        # A static caption over moving video keeps mean luma and diff normal.
        frames = [frame(0, 90, 30)]
        frames += [frame(i * 40, 88 + i % 3, 12) for i in range(1, 40)]
        frames += [frame(40 * 40, 90, 30)]
        data = {"frames": frames, "frame_ms": 40, "windows": windows()}
        result = self._run_cli(data)
        self.assertEqual(result["events"], [])
        self.assertEqual(len(result["playable"]), 1)

    def test_subtitle_on_static_plate_is_flagged_as_freeze(self):
        # Correctly rejecting a *static* caption is not possible from frame
        # statistics alone: it is a genuine frozen picture, so the alarm and
        # its boundaries must be exact.
        frames = [frame(0, 90, 30)]
        frames += [frame(i * 40, 90, 0) for i in range(1, 40)]
        frames.append(frame(40 * 40, 90, 30))
        data = {"frames": frames, "frame_ms": 40,
                "windows": windows(min_freeze_ms=300)}
        result = self._run_cli(data)
        self.assertEqual(
            simple(result["events"]), [("freeze", 40, 1600, 39, "freeze")]
        )

    def test_dynamic_black_background_single_event(self):
        frames = [frame(0, 100, 20)]
        for i in range(1, 20):
            luma = 5 + (i % 4)          # still black, but changing
            diff = 0 if i % 5 == 0 else 6
            frames.append(frame(i * 40, luma, diff))
        frames += [frame(i * 40, 100, 20) for i in range(20, 24)]
        data = {"frames": frames, "frame_ms": 40,
                "windows": windows(min_black_ms=200, gap_ms=45)}
        result = self._run_cli(data)
        self.assertEqual(len(result["events"]), 1)
        event = result["events"][0]
        self.assertEqual(event["kind"], "black")
        self.assertEqual((event["start"], event["end"]), (40, 800))
        self.assertEqual(event["reason"], "both")
        assert_matches_frame_gold(self, frames, data, result)

    def test_reserved_black_is_not_signal_loss(self):
        frames = [frame(0, 100, 20)]
        frames += [frame(i * 40, 8, 0) for i in range(1, 8)]
        frames += [frame(i * 40, 100, 20) for i in range(8, 12)]
        for item in frames[1:8]:
            item["marked_black"] = True
        data = {"frames": frames, "frame_ms": 40,
                "windows": windows(min_black_ms=80)}
        result = self._run_cli(data)
        self.assertEqual(result["events"], [])
        self.assertEqual(
            [(p["start"], p["end"]) for p in result["playable"]],
            [(0, 480)],
        )

    def test_jump_frames_and_backwards_pts(self):
        frames = [
            frame(0, 100, 20),
            frame(40, 10, 20),
            frame(80, 10, 20),
            frame(20, 10, 20),    # backwards -> skipped
            frame(600, 10, 20),   # big forward jump (dropped frames)
            frame(640, 10, 20),
            frame(700, 100, 20),
        ]
        data = {"frames": frames, "frame_ms": 40,
                "windows": windows(min_black_ms=200)}
        result = self._run_cli(data)
        self.assertEqual(result["skipped"], [3])
        assert_matches_frame_gold(self, frames, data, result)
        self.assertEqual(
            simple(result["events"]), [("black", 40, 700, 4, "black")]
        )

    def test_changing_read_block_size_does_not_duplicate_events(self):
        frames = [frame(0, 100, 20)]
        pts = 40
        for i in range(1, 200):
            if 40 <= i < 120:
                frames.append(frame(pts, 10, 0 if i % 6 else 7))
            elif 140 <= i < 170:
                frames.append(frame(pts, 100, 0 if i % 9 else 8))
            else:
                frames.append(frame(pts, 100, 15 if i % 3 else 25))
            pts += 40
        data = {"frames": frames, "frame_ms": 40,
                "windows": windows(min_black_ms=200, min_freeze_ms=300,
                                   gap_ms=60)}
        baseline = self._run_cli(data)
        self.assertTrue(baseline["events"])
        for chunk in (1, 2, 3, 5, 13, 64, len(frames)):
            self.assertEqual(self._run_cli(data, chunk=chunk), baseline)


class EdgeCaseTests(unittest.TestCase):
    def test_empty_input(self):
        out = run([])
        self.assertEqual(out, {"events": [], "playable": [], "skipped": []})

    def test_single_first_frame_black(self):
        out = run([frame(0, 10, 0)], min_black_ms=40)
        self.assertEqual(
            simple(out["events"]), [("black", 0, 40, 1, "black")]
        )

    def test_single_first_frame_not_frozen(self):
        out = run([frame(0, 100, 0)], min_freeze_ms=1)
        self.assertEqual(out["events"], [])

    def test_duplicate_pts_run_uses_single_display_interval(self):
        # All frames share a pts, so the run occupies one display interval,
        # not five.  It must not be able to clear a longer minimum.
        frames = [frame(0, 10, 20)] * 5
        out = run(frames, min_black_ms=41)
        self.assertEqual(out["events"], [])
        out = run(frames, min_black_ms=40)
        self.assertEqual(
            simple(out["events"]), [("black", 0, 40, 5, "black")]
        )

    def test_zero_minimum_accepted(self):
        frames = [frame(0, 100, 20), frame(40, 10, 20),
                  frame(80, 100, 20)]
        out = run(frames, min_black_ms=0)
        self.assertEqual(
            simple(out["events"]), [("black", 40, 80, 1, "black")]
        )


if __name__ == "__main__":
    unittest.main()

