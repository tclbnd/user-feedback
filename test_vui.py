#!/usr/bin/env python3
"""Regression tests for the VUI colour-attribute resolver.

The cases mirror the fault report: a switch to the next configuration
segment of the same codec kept stale colour tags, the new shot jumped in
greyscale/hue, and a missing field reset the whole group.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import vui

HERE = os.path.dirname(os.path.abspath(__file__))

BT709 = "bt709"
BT2020 = "bt2020nc"

FULL = {
    "range": "limited",
    "primaries": "bt709",
    "transfer": "bt709",
    "matrix": "bt709",
}


def run_cli(data):
    """Run the resolver exactly as a caller would and return its output."""
    tmp = tempfile.mkdtemp(dir=os.environ.get("TMPDIR") or HERE)
    src = os.path.join(tmp, "in.json")
    dst = os.path.join(tmp, "out.json")
    with open(src, "w") as fh:
        json.dump(data, fh)
    subprocess.run(
        [sys.executable, os.path.join(HERE, "vui.py"), src, dst],
        check=True,
    )
    with open(dst) as fh:
        return json.load(fh)


def by_index(frames):
    return {(f["seg"], f["n"]): f for f in frames}


def converted(fieldmap):
    return tuple(fieldmap[f] for f in vui.FIELDS)


class NoVuiTest(unittest.TestCase):
    def test_container_fills_every_field(self):
        out = run_cli({
            "segments": [{"id": "a", "frames": 2, "vui": None,
                          "container": dict(FULL)}],
            "queries": [],
        })
        self.assertEqual(len(out["frames"]), 2)
        for rec in out["frames"]:
            self.assertEqual(converted(rec), converted(FULL))
            self.assertEqual(set(rec["source"].values()), {"container"})

    def test_absent_vui_and_container_keys(self):
        out = run_cli({
            "segments": [
                {"id": "a", "frames": 1, "vui": None, "container": {}},
                {"id": "b", "frames": 1},
            ],
            "queries": [],
        })
        for rec in out["frames"]:
            self.assertEqual(converted(rec),
                             ("unspecified",) * len(vui.FIELDS))
        # Nothing was ever declared: the very first frame defaults, later
        # frames carry that resolved "unspecified" state forward.
        self.assertEqual(set(out["frames"][0]["source"].values()),
                         {"default"})
        self.assertEqual(set(out["frames"][1]["source"].values()),
                         {"inherited"})


class ContainerConflictTest(unittest.TestCase):
    def test_vui_wins_without_override(self):
        out = run_cli({
            "segments": [{
                "id": "a", "frames": 1,
                "vui": {"matrix": BT2020},
                "container": {"matrix": "bt601", "range": "full",
                              "override": False},
            }],
            "queries": [],
        })
        rec = out["frames"][0]
        self.assertEqual(rec["matrix"], BT2020)
        self.assertEqual(rec["source"]["matrix"], "vui")
        self.assertEqual(rec["range"], "full")
        self.assertEqual(rec["source"]["range"], "container")

    def test_override_beats_vui_for_declared_fields_only(self):
        out = run_cli({
            "segments": [{
                "id": "a", "frames": 1,
                "vui": {"range": "full", "matrix": BT2020,
                        "primaries": "bt2020", "transfer": "smpte2084"},
                "container": {"range": "limited",
                              "primaries": "bt709", "override": True},
            }],
            "queries": [],
        })
        rec = out["frames"][0]
        self.assertEqual(rec["range"], "limited")
        self.assertEqual(rec["primaries"], "bt709")
        self.assertEqual(rec["source"]["range"], "container")
        self.assertEqual(rec["source"]["primaries"], "container")
        # Undeclared by the container: the VUI's own values still apply.
        self.assertEqual(rec["matrix"], BT2020)
        self.assertEqual(rec["transfer"], "smpte2084")
        self.assertEqual(rec["source"]["matrix"], "vui")
        self.assertEqual(rec["source"]["transfer"], "vui")

    def test_override_with_no_container_values_changes_nothing(self):
        out = run_cli({
            "segments": [{
                "id": "a", "frames": 1,
                "vui": dict(FULL),
                "container": {"override": True},
            }],
            "queries": [],
        })
        rec = out["frames"][0]
        self.assertEqual(converted(rec), converted(FULL))
        self.assertEqual(set(rec["source"].values()), {"vui"})


class PartialUpdateTest(unittest.TestCase):
    def test_missing_fields_are_inherited_per_field(self):
        out = run_cli({
            "segments": [
                {"id": "old", "frames": 2, "vui": dict(FULL),
                 "container": {}},
                {"id": "new", "frames": 2,
                 "vui": {"range": "full"}, "container": {}},
            ],
            "queries": [],
        })
        frames = out["frames"]
        for rec in frames[:2]:
            self.assertEqual(converted(rec), converted(FULL))
        for rec in frames[2:]:
            self.assertEqual(rec["range"], "full")
            self.assertEqual(rec["source"]["range"], "vui")
            for field in ("primaries", "transfer", "matrix"):
                self.assertEqual(rec[field], FULL[field])
                self.assertEqual(rec["source"][field], "inherited")

    def test_missing_field_does_not_reset_whole_group(self):
        out = run_cli({
            "segments": [
                {"id": "a", "frames": 1, "vui": dict(FULL),
                 "container": {}},
                {"id": "b", "frames": 1,
                 "vui": {"matrix": BT2020}, "container": {}},
            ],
            "queries": [],
        })
        rec = out["frames"][1]
        self.assertNotEqual(
            converted(rec),
            ("unspecified",) * len(vui.FIELDS),
        )
        self.assertEqual(rec["matrix"], BT2020)
        self.assertEqual(rec["source"]["matrix"], "vui")
        self.assertEqual(rec["range"], FULL["range"])
        self.assertEqual(rec["source"]["range"], "inherited")

    def test_old_frames_are_not_rewritten_by_later_segments(self):
        data = {
            "segments": [
                {"id": "a", "frames": 2, "vui": dict(FULL),
                 "container": {}},
                {"id": "b", "frames": 2,
                 "vui": {"range": "full", "matrix": BT2020,
                         "primaries": "bt2020", "transfer": "smpte2084"},
                 "container": {}},
            ],
            "queries": [],
        }
        out = run_cli(data)
        for rec in out["frames"][:2]:
            self.assertEqual(converted(rec), converted(FULL))
            self.assertEqual(set(rec["source"].values()), {"vui"})
        # Frame-to-frame state is per frame: both new frames equal, and the
        # first new frame already carries the new configuration.
        self.assertEqual(converted(out["frames"][2]),
                         ("full", "bt2020", "smpte2084", BT2020))
        self.assertEqual(converted(out["frames"][3]),
                         converted(out["frames"][2]))


class TwoTrackMixTest(unittest.TestCase):
    CONFIG_A = {"range": "limited", "primaries": "bt709",
                "transfer": "bt709", "matrix": "bt709"}
    CONFIG_B = {"range": "full", "primaries": "bt2020",
                "transfer": "smpte2084", "matrix": "bt2020nc"}

    def test_alternating_tracks_do_not_bleed(self):
        # Inheritance is per frame, not per track: a segment that declares
        # all its fields fully overrides whatever the other track last set.
        segments = [
            {"id": "A1", "frames": 2, "vui": dict(self.CONFIG_A),
             "container": {}},
            {"id": "B1", "frames": 1, "vui": dict(self.CONFIG_B),
             "container": {}},
            {"id": "A2", "frames": 2, "vui": dict(self.CONFIG_A),
             "container": {}},
        ]
        out = run_cli({"segments": segments, "queries": []})
        expected = [
            ("A1", self.CONFIG_A), ("A1", self.CONFIG_A),
            ("B1", self.CONFIG_B),
            ("A2", self.CONFIG_A), ("A2", self.CONFIG_A),
        ]
        for rec, (seg_id, cfg) in zip(out["frames"], expected):
            self.assertEqual(rec["seg"], seg_id)
            self.assertEqual(converted(rec), converted(cfg))
            self.assertEqual(set(rec["source"].values()), {"vui"})

    def test_partial_track_reuses_only_undeclared_fields(self):
        segments = [
            {"id": "A1", "frames": 1, "vui": dict(self.CONFIG_A),
             "container": {}},
            {"id": "B1", "frames": 1, "vui": dict(self.CONFIG_B),
             "container": {}},
            {"id": "A2", "frames": 1,
             "vui": {"range": self.CONFIG_A["range"]}, "container": {}},
        ]
        out = run_cli({"segments": segments, "queries": []})
        rec = out["frames"][2]
        self.assertEqual(rec["range"], self.CONFIG_A["range"])
        self.assertEqual(rec["source"]["range"], "vui")
        # The other fields were not re-declared, so the last resolved values
        # (from track B) carry over untouched.
        for field in ("primaries", "transfer", "matrix"):
            self.assertEqual(rec[field], self.CONFIG_B[field])
            self.assertEqual(rec["source"][field], "inherited")


class SeekTest(unittest.TestCase):
    def test_seek_to_segment_start_matches_playback_when_complete(self):
        segments = [
            {"id": "a", "frames": 3, "vui": dict(FULL), "container": {}},
            {"id": "b", "frames": 4,
             "vui": {"range": "full", "primaries": "bt2020",
                     "transfer": "smpte2084", "matrix": BT2020},
             "container": {}},
        ]
        queries = [{"at": 0}, {"at": 2}, {"at": 3}, {"at": 6}]
        out = run_cli({"segments": segments, "queries": queries})
        played = out["frames"]
        self.assertEqual(len(out["queries"]), len(queries))
        for q in out["queries"]:
            want = played[q["at"]]
            for field in ("seg", "n") + vui.FIELDS:
                self.assertEqual(q[field], want[field],
                                 "mismatch on %s at %r" % (field, q["at"]))
            self.assertEqual(q["source"], want["source"])

    def test_seek_without_prior_state_does_not_inherit(self):
        segments = [
            {"id": "a", "frames": 2, "vui": dict(FULL), "container": {}},
            {"id": "b", "frames": 3,
             "vui": {"matrix": BT2020}, "container": {}},
        ]
        out = run_cli({"segments": segments,
                       "queries": [{"at": 2}, {"at": 3}]})
        self.assertEqual([q["at"] for q in out["queries"]], [2, 3])
        first, second = out["queries"]
        # Frame 0 of the sought segment has nothing to inherit from, so its
        # undeclared fields default instead of leaking segment a's values.
        self.assertEqual(first["seg"], "b")
        self.assertEqual(first["n"], 0)
        self.assertEqual(first["matrix"], BT2020)
        self.assertEqual(first["source"]["matrix"], "vui")
        for field in ("range", "primaries", "transfer"):
            self.assertEqual(first[field], "unspecified")
            self.assertEqual(first["source"][field], "default")
        # Within the sought segment normal frame inheritance applies.
        self.assertEqual(second["n"], 1)
        self.assertEqual(second["matrix"], BT2020)
        self.assertEqual(second["source"]["matrix"], "vui")
        for field in ("range", "primaries", "transfer"):
            self.assertEqual(second[field], "unspecified")
            self.assertEqual(second["source"][field], "inherited")

    def test_query_in_first_segment(self):
        out = run_cli({
            "segments": [{"id": "a", "frames": 2, "vui": None,
                          "container": dict(FULL)}],
            "queries": [{"at": 1}],
        })
        self.assertEqual(out["queries"][0]["n"], 1)
        self.assertEqual(converted(out["queries"][0]), converted(FULL))
        self.assertEqual(set(out["queries"][0]["source"].values()),
                         {"container"})


def yuv_to_rgb8(y, u, v, rng, matrix):
    """Small fixed-patch converter used to observe greyscale/hue drift."""
    if rng == "limited":
        yf, cbf, crf = (y - 16) / 219.0, (u - 128) / 224.0, (v - 128) / 224.0
    else:
        yf, cbf, crf = y / 255.0, (u - 128) / 255.0, (v - 128) / 255.0
    if matrix in (BT2020, "bt2020c"):
        kr, kb = 0.2627, 0.0593
    elif matrix in ("bt601", "bt470bg", "smpte170m"):
        kr, kb = 0.299, 0.114
    else:
        kr, kb = 0.2126, 0.0722
    kg = 1.0 - kr - kb
    r = yf + 2.0 * (1.0 - kr) * crf
    b = yf + 2.0 * (1.0 - kb) * cbf
    g = (yf - kr * r - kb * b) / kg
    return tuple(round(c * 255.0) for c in (r, g, b))


class FixedPatchTest(unittest.TestCase):
    """A fixed colour block must convert identically for every frame that
    carries the same resolved tags, and must change when tags change."""

    def test_patch_is_stable_within_and_across_matching_segments(self):
        patch = (126, 128, 128)  # mid grey
        out = run_cli({
            "segments": [
                {"id": "a", "frames": 3, "vui": dict(FULL),
                 "container": {}},
                {"id": "b", "frames": 2,
                 "vui": {"matrix": "bt709"}, "container": {}},
            ],
            "queries": [{"at": 2}],
        })
        values = [yuv_to_rgb8(*patch, rng=rec["range"],
                              matrix=rec["matrix"])
                  for rec in out["frames"]]
        self.assertEqual(len(set(values)), 1)
        q = out["queries"][0]
        self.assertEqual(yuv_to_rgb8(*patch, rng=q["range"],
                                     matrix=q["matrix"]), values[0])

    def test_range_change_alters_greyscale_but_not_chroma_balance(self):
        limited = yuv_to_rgb8(126, 128, 128, "limited", "bt709")
        full = yuv_to_rgb8(128, 128, 128, "full", "bt709")
        misread = yuv_to_rgb8(200, 128, 128, "full", "bt709")
        reference = yuv_to_rgb8(200, 128, 128, "limited", "bt709")
        self.assertEqual(len(set(limited)), 1)      # neutral stays neutral
        self.assertEqual(limited, full)
        self.assertGreater(abs(misread[0] - reference[0]), 2)

    def test_matrix_change_shifts_hue(self):
        patch = (126, 90, 200)  # saturated block
        as_709 = yuv_to_rgb8(*patch, "limited", "bt709")
        as_601 = yuv_to_rgb8(*patch, "limited", "bt601")
        self.assertNotEqual(as_709, as_601)


if __name__ == "__main__":
    unittest.main()

