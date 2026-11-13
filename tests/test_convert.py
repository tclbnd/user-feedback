"""Acceptance test suite for the offline HDR frame converter.

Run with:  python3 -m unittest tests.test_convert -v
(from the directory containing convert.py)
"""
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import convert  # noqa: E402
import reference  # noqa: E402

SEED = 20261113


# ---------------------------------------------------------------------------
# Input builders
# ---------------------------------------------------------------------------

def make_frame(rng, w, h, siting="center", color_range="limited",
               chroma_pattern="random"):
    cw, ch = (w + 1) // 2, (h + 1) // 2
    y = [[rng.randint(64, 940) for _ in range(w)] for _ in range(h)]
    if chroma_pattern == "random":
        cb = [[rng.randint(64, 960) for _ in range(cw)] for _ in range(ch)]
        cr = [[rng.randint(64, 960) for _ in range(cw)] for _ in range(ch)]
    elif chroma_pattern == "neutral":
        cb = [[512] * cw for _ in range(ch)]
        cr = [[512] * cw for _ in range(ch)]
    elif chroma_pattern == "extreme":
        # Negative intermediate colour vectors: deeply saturated edge codes.
        cb = [[rng.choice((64, 960)) for _ in range(cw)] for _ in range(ch)]
        cr = [[rng.choice((64, 960)) for _ in range(cw)] for _ in range(ch)]
    else:
        raise ValueError(chroma_pattern)
    return {
        "width": w,
        "height": h,
        "metadata": {
            "primaries": "bt2020",
            "transfer": "pq",
            "matrix": "bt2020nc",
            "range": color_range,
            "siting": siting,
        },
        "y": y,
        "cb": cb,
        "cr": cr,
    }


def gray_ramp_frame(w, h, siting="center"):
    """Deterministic grey ramp spanning the full limited luma range."""
    cw, ch = (w + 1) // 2, (h + 1) // 2
    y = [
        [64 + int(876 * (x + y * w) / max(1, w * h - 1))
         for x in range(w)]
        for y in range(h)
    ]
    return {
        "width": w,
        "height": h,
        "metadata": {
            "primaries": "bt2020", "transfer": "pq", "matrix": "bt2020nc",
            "range": "limited", "siting": siting,
        },
        "y": y,
        "cb": [[512] * cw for _ in range(ch)],
        "cr": [[512] * cw for _ in range(ch)],
    }


def color_edge_frame(w, h, siting):
    """Sharp chroma edges at all quadrant boundaries."""
    cw, ch = (w + 1) // 2, (h + 1) // 2
    frame = gray_ramp_frame(w, h, siting)
    frame["cb"] = [
        [960 if (i < cw // 2) == (j % 2 == 0) else 64 for i in range(cw)]
        for j in range(ch)
    ]
    frame["cr"] = [
        [64 if i + j % 2 < cw else 960 for i in range(cw)]
        for j in range(ch)
    ]
    return frame


def planes_of(frame):
    return frame["y"], frame["cb"], frame["cr"]


def max_plane_diff(a, b):
    diff = 0
    for pa, pb in ((a["y"], b["y"]), (a["cb"], b["cb"]),
                   (a["cr"], b["cr"])):
        for ra, rb in zip(pa, pb):
            for va, vb in zip(ra, rb):
                diff = max(diff, abs(va - vb))
    return diff


# ---------------------------------------------------------------------------
# 1. Single frame vs independent floating-point path
# ---------------------------------------------------------------------------

class SingleFrameReferenceTests(unittest.TestCase):

    def setUp(self):
        self.rng = random.Random(SEED)

    def _assert_matches_reference(self, frame, target=None):
        out = convert.convert(frame, seed=None, debug=True)
        ref_y, ref_cb, ref_cr, ref_debug, _ = reference.reference_pipeline(
            frame, convert.SINGLE_WHITE_NITS,
            target or frame["metadata"]["siting"],
        )
        self.assertEqual(out["y"], ref_y)
        self.assertEqual(out["cb"], ref_cb)
        self.assertEqual(out["cr"], ref_cr)
        # Debug stages must be the same finite floats bit-for-bit in shape
        # and within floating-point tolerance in value.
        for key in ("rgb2020_nits", "rgb709_nits", "mapped_linear709"):
            got = out["debug"][key]
            exp = ref_debug[key]
            self.assertEqual((len(got), len(got[0]), len(got[0][0])),
                             (len(exp), len(exp[0]), 3))
            for gy, ey in zip(got, exp):
                for gp, ep in zip(gy, ey):
                    for gv, ev in zip(gp, ep):
                        self.assertTrue(math.isfinite(gv))
                        scale = max(1.0, abs(ev))
                        self.assertLessEqual(
                            abs(gv - ev), 1e-10 * scale)

    def test_grey_ramp_both_sitings_odd_sizes(self):
        for siting in ("center", "left"):
            for w, h in ((1, 1), (2, 2), (3, 3), (5, 3), (3, 5),
                         (7, 4), (4, 7), (9, 9)):
                self._assert_matches_reference(
                    gray_ramp_frame(w, h, siting))

    def test_chroma_edges_both_sitings(self):
        for siting in ("center", "left"):
            for w, h in ((4, 4), (5, 5), (6, 3), (3, 6)):
                self._assert_matches_reference(
                    color_edge_frame(w, h, siting))

    def test_negative_intermediate_vectors(self):
        for siting in ("center", "left"):
            frame = make_frame(self.rng, 9, 7, siting,
                               chroma_pattern="extreme")
            self._assert_matches_reference(frame)

    def test_random_frames_odd_sizes_and_ranges(self):
        for _ in range(12):
            w = self.rng.randint(1, 13)
            h = self.rng.randint(1, 11)
            siting = self.rng.choice(("center", "left"))
            rng_kind = self.rng.choice(("limited", "full"))
            pattern = self.rng.choice(("random", "neutral", "extreme"))
            frame = make_frame(self.rng, w, h, siting, rng_kind, pattern)
            if rng_kind == "full":
                # Single-frame CLI only accepts limited range.
                with self.assertRaises(ValueError):
                    convert.convert(frame)
                frame["metadata"]["range"] = "limited"
            self._assert_matches_reference(frame)

    def test_output_contract(self):
        frame = gray_ramp_frame(5, 3, "left")
        out = convert.convert(frame)
        self.assertEqual(out["width"], 5)
        self.assertEqual(out["height"], 3)
        self.assertEqual(out["metadata"], {
            "primaries": "bt709", "transfer": "bt709", "matrix": "bt709",
            "range": "limited", "siting": "left",
        })
        cw, ch = 3, 2
        self.assertEqual((len(out["y"]), len(out["y"][0])), (3, 5))
        self.assertEqual((len(out["cb"]), len(out["cb"][0])), (ch, cw))
        for row in out["y"]:
            self.assertTrue(all(16 <= v <= 235 for v in row))
        for plane in (out["cb"], out["cr"]):
            for row in plane:
                self.assertTrue(all(16 <= v <= 240 for v in row))
        self.assertNotIn("debug", out)

    def test_dither_within_half_code_and_stable(self):
        frame = make_frame(self.rng, 6, 4)
        no_dither = convert.convert(frame, seed=None)
        d1 = convert.convert(frame, seed=42)
        d2 = convert.convert(frame, seed=42)
        self.assertEqual(d1["y"], d2["y"])
        self.assertEqual(d1["cb"], d2["cb"])
        self.assertEqual(d1["cr"], d2["cr"])
        self.assertLessEqual(max_plane_diff(no_dither, d1), 1)
        # Different seeds may move values but must stay in legal code ranges.
        d3 = convert.convert(frame, seed=7)
        for plane in (d3["y"], d3["cb"], d3["cr"]):
            for row in plane:
                for v in row:
                    self.assertIsInstance(v, int)
        self.assertTrue(all(16 <= v <= 235 for row in d3["y"] for v in row))

    def test_metadata_rejection(self):
        good = gray_ramp_frame(2, 2)
        bad_cases = [
            {"transfer": "hlg"},
            {"primaries": "bt709"},
            {"matrix": "bt709"},
            {"siting": "topleft"},
            {"range": "full"},
        ]
        for case in bad_cases:
            frame = json.loads(json.dumps(good))
            frame["metadata"].update(case)
            with self.assertRaises(ValueError):
                convert.convert(frame)
        frame = json.loads(json.dumps(good))
        del frame["metadata"]["matrix"]
        with self.assertRaises(ValueError):
            convert.convert(frame)
        with self.assertRaises(ValueError):
            convert.convert({})


# ---------------------------------------------------------------------------
# 2. Sequence statistics vs offline sorted truth
# ---------------------------------------------------------------------------

def build_sequence(rng, frames_spec, target_siting="center"):
    """frames_spec: list of (w, h, siting, range, duration, cut, pattern)."""
    frames = []
    pts = 0
    for idx, (w, h, siting, rng_kind, dur, cut, pattern) in enumerate(
            frames_spec):
        frame = make_frame(rng, w, h, siting, rng_kind, pattern)
        frames.append({
            "pts": pts,
            "duration": dur,
            **({"cut": True} if cut else {}),
            "frame": frame,
        })
        pts += dur
    return {
        "time_base": [1, 1000],
        "target_siting": target_siting,
        "frames": frames,
    }


def split_shots(entries):
    shots = []
    current = []
    for idx, entry in enumerate(entries):
        if idx == 0 or entry.get("cut"):
            current = [entry]
            shots.append(current)
        else:
            current.append(entry)
    return shots


class SequenceStatisticsTests(unittest.TestCase):

    def setUp(self):
        self.rng = random.Random(SEED + 1)

    def test_weighted_p95_and_white_against_sorted_truth(self):
        spec = [
            # shot 0: mixed durations, odd size, per-frame siting/range.
            (5, 3, "center", "limited", 3, True, "random"),
            (5, 3, "left", "limited", 7, False, "extreme"),
            (5, 3, "left", "full", 5, False, "neutral"),
            # cut with a dimension change.
            (4, 6, "center", "limited", 2, True, "random"),
            (4, 6, "left", "full", 11, False, "extreme"),
            # third shot.
            (7, 7, "left", "limited", 4, True, "random"),
        ]
        data = build_sequence(self.rng, spec)
        result = convert.convert_sequence(data)

        truth_shots = split_shots(data["frames"])
        self.assertEqual(len(result["shots"]), len(truth_shots))
        for got, entries in zip(result["shots"], truth_shots):
            ticks, p95, white = reference.reference_shot_stats(entries)
            self.assertEqual(got["pixel_ticks"], ticks)
            self.assertEqual(got["p95_nits"], p95)
            self.assertAlmostEqual(got["white_nits"], white, places=12)
            self.assertEqual(got["start_pts"], entries[0]["pts"])
            self.assertGreaterEqual(got["white_nits"], 203.0)

    def test_frame_outputs_match_reference_with_shot_white(self):
        spec = [
            (5, 5, "center", "limited", 2, True, "random"),
            (5, 5, "left", "limited", 9, False, "extreme"),
            (3, 7, "center", "full", 4, True, "random"),
            (3, 7, "center", "limited", 1, False, "neutral"),
        ]
        data = build_sequence(self.rng, spec, target_siting="left")
        result = convert.convert_sequence(data, seed=None, debug=True)
        truth_shots = split_shots(data["frames"])
        white_by_start = {
            shot["start_pts"]: shot["white_nits"] for shot in result["shots"]
        }
        for out_entry, in_entry in zip(result["frames"], data["frames"]):
            self.assertEqual(out_entry["pts"], in_entry["pts"])
            self.assertEqual(out_entry["duration"], in_entry["duration"])
            shot_start = out_entry["shot_start_pts"]
            frame = in_entry["frame"]
            ry, rcb, rcr, rdbg, _ = reference.reference_pipeline(
                frame, white_by_start[shot_start], "left")
            of = out_entry["frame"]
            self.assertEqual(of["y"], ry)
            self.assertEqual(of["cb"], rcb)
            self.assertEqual(of["cr"], rcr)
            self.assertEqual(of["metadata"]["siting"], "left")
            for key in ("rgb2020_nits", "rgb709_nits", "mapped_linear709"):
                self.assertEqual(len(of["debug"][key]), frame["height"])
                self.assertEqual(len(of["debug"][key][0]), frame["width"])

    def test_siting_changes_are_reflected(self):
        frame = make_frame(self.rng, 6, 4, "center", "limited", "extreme")
        base = {"time_base": [1, 25], "target_siting": "center", "frames": [
            {"pts": 0, "duration": 1, "frame": frame}]}
        out_center = convert.convert_sequence(base)["frames"][0]["frame"]
        base_left = json.loads(json.dumps(base))
        base_left["target_siting"] = "left"
        out_left = convert.convert_sequence(base_left)["frames"][0]["frame"]
        # Y is siting-independent for the same frame; chroma must differ.
        self.assertEqual(out_center["y"], out_left["y"])
        self.assertNotEqual(out_center["cb"], out_left["cb"])
        self.assertNotEqual(out_center["cr"], out_left["cr"])

    def test_short_highlights_do_not_move_static_regions(self):
        static = make_frame(self.rng, 8, 6, "center", "limited", "random")
        highlight = json.loads(json.dumps(static))
        for y in range(6):
            for x in range(8):
                highlight["y"][y][x] = 940  # near-PQ-peak burst
        entries = []
        pts = 0
        for dur, frame in [(10, static), (10, static), (1, highlight),
                           (10, static), (1, highlight), (10, static)]:
            entries.append({"pts": pts, "duration": dur, "frame": frame})
            pts += dur
        data = {"time_base": [1, 90000], "target_siting": "center",
                "frames": entries}
        result = convert.convert_sequence(data)
        baseline = result["frames"][0]["frame"]
        # Every frame uses the single shot white parameter; static frames
        # must produce byte-identical planes regardless of the bursts.
        static_indices = [0, 1, 3, 5]
        for idx in static_indices:
            self.assertEqual(result["frames"][idx]["frame"]["y"],
                             baseline["y"])
            self.assertEqual(result["frames"][idx]["frame"]["cb"],
                             baseline["cb"])
            self.assertEqual(result["frames"][idx]["frame"]["cr"],
                             baseline["cr"])
        # A longer highlight changes the bounded shot statistics but never
        # retroactively changes earlier frames once re-run.
        long_entries = json.loads(json.dumps(entries))
        long_entries[2]["duration"] = 40
        pts2 = 0
        for entry in long_entries:
            entry["pts"] = pts2
            pts2 += entry["duration"]
        long_data = {"time_base": [1, 90000], "target_siting": "center",
                     "frames": long_entries}
        long_result = convert.convert_sequence(long_data)
        self.assertNotEqual(
            result["shots"][0]["p95_nits"],
            long_result["shots"][0]["p95_nits"],
        )
        # Static region still invariant within the longer-highlight run.
        base2 = long_result["frames"][0]["frame"]
        for idx in static_indices:
            self.assertEqual(long_result["frames"][idx]["frame"]["y"],
                             base2["y"])


# ---------------------------------------------------------------------------
# 3. Determinism, re-run from a cut and timestamp preservation
# ---------------------------------------------------------------------------

class DeterminismTests(unittest.TestCase):

    def setUp(self):
        self.rng = random.Random(SEED + 2)

    def _three_shot_sequence(self):
        spec = [
            (4, 4, "center", "limited", 5, True, "random"),
            (4, 4, "left", "full", 3, False, "extreme"),
            (6, 2, "left", "limited", 8, True, "random"),
            (6, 2, "center", "limited", 2, False, "neutral"),
            (3, 5, "left", "limited", 6, True, "extreme"),
            (3, 5, "left", "full", 4, False, "random"),
        ]
        return build_sequence(self.rng, spec, target_siting="left")

    def test_repeated_runs_identical_with_seed(self):
        data = self._three_shot_sequence()
        r1 = convert.convert_sequence(data, seed=99, debug=True)
        r2 = convert.convert_sequence(data, seed=99, debug=True)
        self.assertEqual(json.dumps(r1, sort_keys=True),
                         json.dumps(r2, sort_keys=True))

    def test_tail_rerun_from_cut_matches_full_run(self):
        data = self._three_shot_sequence()
        full = convert.convert_sequence(data, seed=1234)

        # Independently replay the frames starting at the second hard cut.
        cut_index = next(
            i for i, e in enumerate(data["frames"]) if e.get("cut"))
        tail_entries = json.loads(json.dumps(data["frames"][cut_index:]))
        tail = {
            "time_base": data["time_base"],
            "target_siting": data["target_siting"],
            "frames": tail_entries,
        }
        tail_result = convert.convert_sequence(tail, seed=1234)

        # Shot statistics of the replayed shot must match the full run.
        full_shots = {s["start_pts"]: s for s in full["shots"]}
        for shot in tail_result["shots"]:
            same = full_shots[shot["start_pts"]]
            self.assertEqual(shot["p95_nits"], same["p95_nits"])
            self.assertEqual(shot["white_nits"], same["white_nits"])
            self.assertEqual(shot["pixel_ticks"], same["pixel_ticks"])
        # PTS / durations preserved and frames identical.
        for tail_entry, full_entry in zip(
                tail_result["frames"], full["frames"][cut_index:]):
            self.assertEqual(tail_entry["pts"], full_entry["pts"])
            self.assertEqual(tail_entry["duration"],
                             full_entry["duration"])
            self.assertEqual(
                json.dumps(tail_entry["frame"], sort_keys=True),
                json.dumps(full_entry["frame"], sort_keys=True),
            )

    def test_time_base_and_pts_preserved(self):
        data = self._three_shot_sequence()
        result = convert.convert_sequence(data)
        self.assertEqual(result["time_base"], [1, 1000])
        self.assertEqual([f["pts"] for f in result["frames"]],
                         [f["pts"] for f in data["frames"]])
        self.assertEqual([f["duration"] for f in result["frames"]],
                         [f["duration"] for f in data["frames"]])
        starts = [f["shot_start_pts"] for f in result["frames"]]
        self.assertEqual(starts, [0, 0, 8, 8, 18, 18])


# ---------------------------------------------------------------------------
# 4. Validation failures and atomic output preservation
# ---------------------------------------------------------------------------

class ValidationAndAtomicityTests(unittest.TestCase):

    def setUp(self):
        self.rng = random.Random(SEED + 3)
        self.good = build_sequence(self.rng, [
            (4, 4, "center", "limited", 5, True, "random"),
            (4, 4, "left", "limited", 3, False, "random"),
            (2, 2, "left", "limited", 2, True, "random"),
        ])

    def _expect_failure(self, mutate):
        data = json.loads(json.dumps(self.good))
        mutate(data)
        with self.assertRaises((ValueError, KeyError, TypeError)):
            convert.convert_sequence(data)

    def test_overlapping_and_bad_timestamps_rejected(self):
        def overlap(d):
            d["frames"][1]["pts"] = d["frames"][0]["pts"] + 2
        self._expect_failure(overlap)
        self._expect_failure(lambda d: d["frames"][1].__setitem__(
            "pts", d["frames"][0]["pts"]))
        self._expect_failure(lambda d: d["frames"][0].__setitem__(
            "duration", 0))
        self._expect_failure(lambda d: d["frames"][0].__setitem__(
            "duration", -3))
        self._expect_failure(lambda d: d["frames"][1].__setitem__(
            "pts", 1.5))
        self._expect_failure(lambda d: d.__setitem__("time_base", [1, 0]))
        self._expect_failure(lambda d: d.__setitem__("time_base", [1]))
        self._expect_failure(lambda d: d.__setitem__("frames", []))

    def test_dimension_change_without_cut_rejected(self):
        def shrink(d):
            f = d["frames"][1]["frame"]
            f["width"], f["height"] = 3, 3
            f["y"] = f["y"][:3]
            for row in f["y"]:
                del row[3]
            del f["cb"][1]
            del f["cr"][1]
            for row in f["cb"]:
                del row[1]
            for row in f["cr"]:
                del row[1]
        self._expect_failure(shrink)
        # The same change at a cut must succeed.
        data = json.loads(json.dumps(self.good))
        data["frames"][1]["cut"] = True
        data["frames"][1]["frame"] = make_frame(
            self.rng, 3, 3, "left", "limited", "random")
        convert.convert_sequence(data)

    def test_conflicting_color_declarations_rejected(self):
        self._expect_failure(lambda d: d["frames"][0]["frame"][
            "metadata"].__setitem__("transfer", "hlg"))
        self._expect_failure(lambda d: d["frames"][0]["frame"][
            "metadata"].__setitem__("siting", "weird"))
        self._expect_failure(lambda d: d.__setitem__(
            "target_siting", "top"))
        def bad_cut_flag(d):
            d["frames"][0]["cut"] = "yes"
        self._expect_failure(bad_cut_flag)

    def test_existing_output_survives_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            in_path = tmp / "in.json"
            out_path = tmp / "out.json"
            in_path.write_text(json.dumps(self.good))
            sentinel = '{"sentinel": "do not overwrite"}\n'
            out_path.write_text(sentinel)

            bad = json.loads(json.dumps(self.good))
            bad["frames"][1]["pts"] = 0  # overlap -> failure
            (tmp / "bad.json").write_text(json.dumps(bad))
            proc = subprocess.run(
                [sys.executable, str(ROOT / "convert.py"),
                 str(tmp / "bad.json"), str(out_path), "--sequence"],
                capture_output=True, text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(out_path.read_text(), sentinel)
            self.assertFalse(
                list(tmp.glob(".*.tmp-*")),
                "temporary files must be cleaned up after failure")

            # A successful run does replace the output atomically.
            proc = subprocess.run(
                [sys.executable, str(ROOT / "convert.py"),
                 str(in_path), str(out_path), "--sequence",
                 "--seed", "5"],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            written = json.loads(out_path.read_text())
            self.assertIn("shots", written)
            self.assertFalse(list(tmp.glob(".*.tmp-*")))

    def test_single_frame_cli_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            in_path = tmp / "frame.json"
            out_path = tmp / "frame_out.json"
            in_path.write_text(json.dumps(gray_ramp_frame(3, 3)))
            proc = subprocess.run(
                [sys.executable, str(ROOT / "convert.py"),
                 str(in_path), str(out_path), "--debug"],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(out_path.read_text())
            self.assertEqual(result["metadata"]["primaries"], "bt709")
            self.assertIn("debug", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
