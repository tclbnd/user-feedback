#!/usr/bin/env python3
"""Regression tests for the offline cue resolver (see README.md)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

import cues


def cue(cue_id, component, cue_type, version=1, at=None, target=None):
    item = {"id": cue_id, "component": component, "type": cue_type, "version": version}
    if at is not None:
        item["at"] = at
    if target is not None:
        item["target"] = target
    return item


def component(component_id, time_base=0):
    return {"id": component_id, "time_base": time_base}


def program(program_id, components, cue_list):
    return {"id": program_id, "components": components, "cues": cue_list}


def cuts_by_cue(result, program_id):
    return result["programs"][program_id]["cuts"]


class CueTypeTests(unittest.TestCase):
    def test_immediate_and_at_use_programme_time(self):
        doc = {"programs": [program("p", [component("A", 500)], [
            cue("now", "A", "immediate", at=40),
            cue("later", "A", "at", at=90),
        ])]}
        cuts = cuts_by_cue(cues.resolve(doc), "p")
        self.assertEqual(
            [(c["cue"], c["at"], c["state"]) for c in cuts],
            [("now", 40, "active"), ("later", 90, "active")],
        )

    def test_per_component_maps_each_local_time(self):
        doc = {"programs": [program("p", [
            component("main", 0),
            component("backup", 120),
            component("aux", 400),
        ], [
            cue("pc", "ignored", "per-component", at=30),
        ])]}
        cuts = cuts_by_cue(cues.resolve(doc), "p")
        self.assertEqual(
            [(c["component"], c["at"]) for c in cuts],
            [("main", 30), ("backup", 150), ("aux", 430)],
        )
        self.assertTrue(all(c["cue"] == "pc" for c in cuts))
        self.assertTrue(all(c["state"] == "active" for c in cuts))

    def test_cancel_produces_no_cut_of_its_own(self):
        doc = {"programs": [program("p", [component("A")], [
            cue("x", "A", "at", at=10),
            cue("kill", "A", "cancel", target="x"),
        ])]}
        cuts = cuts_by_cue(cues.resolve(doc), "p")
        self.assertEqual([(c["cue"], c["state"]) for c in cuts], [("x", "cancelled")])


class VersionTests(unittest.TestCase):
    def test_higher_version_revises_same_component_cut(self):
        doc = {"programs": [program("p", [component("A"), component("B")], [
            cue("m", "A", "at", version=1, at=10),
            cue("m", "A", "at", version=2, at=25),
        ])]}
        cuts = cuts_by_cue(cues.resolve(doc), "p")
        self.assertEqual(
            [(c["at"], c["state"], c["version"]) for c in cuts],
            [(10, "superseded", 1), (25, "active", 2)],
        )

    def test_equal_version_keeps_first(self):
        doc = {"programs": [program("p", [component("A")], [
            cue("m", "A", "at", version=4, at=10),
            cue("m", "A", "at", version=4, at=80),
        ])]}
        cuts = cuts_by_cue(cues.resolve(doc), "p")
        self.assertEqual(
            [(c["at"], c["state"]) for c in cuts],
            [(10, "active"), (80, "superseded")],
        )

    def test_revision_does_not_cross_components(self):
        doc = {"programs": [program("p", [component("A"), component("B")], [
            cue("m", "A", "at", version=1, at=10),
            cue("m", "B", "at", version=9, at=20),
        ])]}
        cuts = cuts_by_cue(cues.resolve(doc), "p")
        self.assertEqual(
            [(c["component"], c["at"], c["state"]) for c in cuts],
            [("A", 10, "active"), ("B", 20, "active")],
        )

    def test_per_component_revision_is_per_component(self):
        doc = {"programs": [program("p", [component("A"), component("B")], [
            cue("m", "ignored", "per-component", version=1, at=10),
            cue("m", "B", "at", version=2, at=70),
        ])]}
        cuts = cuts_by_cue(cues.resolve(doc), "p")
        state = {(c["component"], c["at"]): c["state"] for c in cuts}
        self.assertEqual(state, {("A", 10): "active", ("B", 10): "superseded",
                                 ("B", 70): "active"})

    def test_revision_does_not_cross_programs(self):
        doc = {"programs": [
            program("main", [component("A")], [
                cue("shared", "A", "at", version=1, at=10),
            ]),
            program("backup", [component("A")], [
                cue("shared", "A", "at", version=9, at=99),
            ]),
        ]}
        result = cues.resolve(doc)
        self.assertEqual([c["at"] for c in cuts_by_cue(result, "main")],
                         [10])
        self.assertEqual([c["at"] for c in cuts_by_cue(result, "backup")],
                         [99])


class CancelTests(unittest.TestCase):
    def test_cancel_is_scoped_to_its_program(self):
        doc = {"programs": [
            program("main", [component("A")], [
                cue("m", "A", "at", at=10),
                cue("k", "A", "cancel", target="m"),
            ]),
            program("backup", [component("A")], [
                cue("m", "A", "at", at=10),
            ]),
        ]}
        result = cues.resolve(doc)
        self.assertEqual([c["state"] for c in cuts_by_cue(result, "main")],
                         ["cancelled"])
        self.assertEqual([c["state"] for c in cuts_by_cue(result, "backup")],
                         ["active"])

    def test_cancel_is_scoped_to_its_component(self):
        doc = {"programs": [program("p", [component("A"), component("B")], [
            cue("m", "A", "at", at=10),
            cue("m", "B", "at", at=20),
            cue("k", "A", "cancel", target="m"),
        ])]}
        cuts = cuts_by_cue(cues.resolve(doc), "p")
        self.assertEqual(
            [(c["component"], c["state"]) for c in cuts],
            [("A", "cancelled"), ("B", "active")],
        )

    def test_cancel_of_per_component_marker_removes_the_cue(self):
        doc = {"programs": [program("p", [component("A"), component("B")], [
            cue("pc", "ignored", "per-component", at=10),
            cue("k", "A", "cancel", target="pc"),
        ])]}
        cuts = cuts_by_cue(cues.resolve(doc), "p")
        # The per-component cue is bound to every component, so a cancel on
        # any of them removes the whole marker.
        self.assertTrue(all(c["state"] == "cancelled" for c in cuts))

    def test_cancel_then_restore(self):
        doc = {"programs": [program("p", [component("A")], [
            cue("m", "A", "at", version=1, at=10),
            cue("k", "A", "cancel", target="m"),
            cue("m", "A", "at", version=2, at=55),
        ])]}
        cuts = cuts_by_cue(cues.resolve(doc), "p")
        self.assertEqual(
            [(c["at"], c["state"], c["version"]) for c in cuts],
            [(10, "cancelled", 1), (55, "active", 2)],
        )

    def test_cancel_ignores_target_that_never_existed(self):
        doc = {"programs": [program("p", [component("A")], [
            cue("k", "A", "cancel", target="ghost"),
            cue("m", "A", "at", at=10),
        ])]}
        cuts = cuts_by_cue(cues.resolve(doc), "p")
        self.assertEqual([(c["cue"], c["state"]) for c in cuts], [("m", "active")])


class ResumeTests(unittest.TestCase):
    def test_resume_matches_suffix_of_full_scan(self):
        doc = {
            "programs": [program("p", [
                component("main", 0), component("backup", 100),
            ], [
                cue("a", "main", "immediate", at=10),
                cue("b", "backup", "at", at=140),
                cue("pc", "ignored", "per-component", at=30),
                cue("gone", "main", "at", at=180),
                cue("k", "main", "cancel", target="gone"),
            ])],
            "resume": [{"program": "p", "from": point}
                       for point in (0, 30, 100, 170, 1000)],
        }
        result = cues.resolve(doc)
        full = sorted(c["cue"] for c in cuts_by_cue(result, "p")
                      if c["state"] == "active")
        self.assertEqual(result["resume"][0]["cuts"], full)
        for entry in result["resume"][1:]:
            expected = sorted(
                c["cue"] for c in cuts_by_cue(result, "p")
                if c["state"] == "active" and c["at"] >= entry["from"]
            )
            self.assertEqual(entry["cuts"], expected)


class SyntheticProgrammeTests(unittest.TestCase):
    """Cross-programme reuse, duplicates, restore, delay and desync together."""

    def setUp(self):
        self.document = {"programs": [
            program("main", [
                component("V", 0), component("A", 60), component("D", 200),
            ], [
                cue("open", "V", "immediate", at=15),
                cue("bump", "A", "at", version=1, at=90),
                cue("bump", "A", "at", version=2, at=120),
                cue("all", "ignored", "per-component", at=25),
                cue("temp", "V", "at", at=200),
                cue("stop", "V", "cancel", target="temp"),
                cue("temp", "V", "at", version=2, at=260),
                cue("dup", "V", "at", at=300),
                cue("dup", "V", "at", at=310),
                {"id": "missed", "component": "V", "type": "at", "at": 340,
                 "version": 1},
            ]),
            program("backup", [component("V", 0)], [
                cue("open", "V", "immediate", at=15),
                cue("bump", "V", "at", at=400),
                cue("stop", "V", "cancel", target="bump"),
                cue("open", "V", "cancel", target="open"),
            ]),
        ]}

    def test_main_cuts(self):
        cuts = cuts_by_cue(cues.resolve(self.document), "main")
        self.assertEqual(
            [(c["cue"], c["component"], c["at"], c["state"], c["version"])
             for c in cuts],
            [
                ("open", "V", 15, "active", 1),
                ("all", "V", 25, "active", 1),
                ("all", "A", 85, "active", 1),
                ("bump", "A", 90, "superseded", 1),
                ("bump", "A", 120, "active", 2),
                ("temp", "V", 200, "cancelled", 1),
                ("all", "D", 225, "active", 1),
                ("temp", "V", 260, "active", 2),
                ("dup", "V", 300, "active", 1),
                ("dup", "V", 310, "superseded", 1),
                ("missed", "V", 340, "active", 1),
            ],
        )

    def test_backup_is_independent(self):
        cuts = cuts_by_cue(cues.resolve(self.document), "backup")
        self.assertEqual(
            [(c["cue"], c["at"], c["state"]) for c in cuts],
            [("open", 15, "cancelled"), ("bump", 400, "cancelled")],
        )

    def test_resume_positions(self):
        document = dict(self.document)
        document["resume"] = [{"program": "main", "from": 120},
                              {"program": "main", "from": 259},
                              {"program": "main", "from": 330},
                              {"program": "backup", "from": 0}]
        resume = cues.resolve(document)["resume"]
        self.assertEqual(resume[0]["cuts"], ["all", "bump", "dup", "missed", "temp"])
        self.assertEqual(resume[1]["cuts"], ["dup", "missed", "temp"])
        self.assertEqual(resume[2]["cuts"], ["missed"])
        self.assertEqual(resume[3]["cuts"], [])


class InputHandlingTests(unittest.TestCase):
    def test_input_document_is_not_mutated(self):
        doc = {"programs": [program("p", [component("A")], [
            cue("m", "A", "at", at=10),
            cue("m", "A", "at", at=20),
            cue("k", "A", "cancel", target="m"),
        ])]}
        snapshot = json.dumps(doc, sort_keys=True)
        cues.resolve(doc)
        self.assertEqual(json.dumps(doc, sort_keys=True), snapshot)

    def test_cli_round_trip(self):
        doc = {"programs": [program("p", [component("A", 40)], [
            cue("pc", "ignored", "per-component", at=5),
        ])], "resume": [{"program": "p", "from": 0}]}
        with tempfile.TemporaryDirectory() as workdir:
            source = os.path.join(workdir, "in.json")
            target = os.path.join(workdir, "out.json")
            with open(source, "w", encoding="utf-8") as handle:
                json.dump(doc, handle)
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "cues.py")
            subprocess.run([sys.executable, "-B", script, source, target],
                           check=True)
            with open(target, "r", encoding="utf-8") as handle:
                output = json.load(handle)
        self.assertEqual(output["programs"]["p"]["cuts"],
                         [{"cue": "pc", "component": "A", "at": 45,
                           "state": "active", "version": 1}])
        self.assertEqual(output["resume"],
                         [{"program": "p", "from": 0, "cuts": ["pc"]}])


class EdgeCaseTests(unittest.TestCase):
    def test_programme_without_components(self):
        doc = {"programs": [program("empty", [], [
            cue("pc", "ignored", "per-component", at=5),
        ])]}
        self.assertEqual(cuts_by_cue(cues.resolve(doc), "empty"), [])

    def test_programme_without_cues(self):
        doc = {"programs": [program("idle", [component("A")], [])]}
        self.assertEqual(cuts_by_cue(cues.resolve(doc), "idle"), [])

    def test_missing_version_defaults_to_zero(self):
        bare = {"id": "m", "component": "A", "type": "at", "at": 10}
        doc = {"programs": [program("p", [component("A")], [
            bare,
            cue("m", "A", "at", version=1, at=20),
        ])]}
        cuts = cuts_by_cue(cues.resolve(doc), "p")
        self.assertEqual(
            [(c["version"], c["state"]) for c in cuts],
            [(0, "superseded"), (1, "active")],
        )

    def test_lower_version_after_cancel_does_not_restore(self):
        doc = {"programs": [program("p", [component("A")], [
            cue("m", "A", "at", version=3, at=10),
            cue("k", "A", "cancel", target="m"),
            cue("m", "A", "at", version=2, at=50),
        ])]}
        cuts = cuts_by_cue(cues.resolve(doc), "p")
        self.assertEqual(
            [(c["at"], c["state"], c["version"]) for c in cuts],
            [(10, "cancelled", 3), (50, "superseded", 2)],
        )

    def test_cancel_versioning_keeps_only_the_winning_cancel(self):
        # The higher-version cancel is the one that fires; the stale cancel
        # must not take effect at its earlier position.
        doc = {"programs": [program("p", [component("A")], [
            cue("m", "A", "at", at=10),
            cue("k", "A", "cancel", version=1, target="nothing"),
            cue("k", "A", "cancel", version=2, target="m"),
        ])]}
        cuts = cuts_by_cue(cues.resolve(doc), "p")
        self.assertEqual([(c["cue"], c["state"]) for c in cuts],
                         [("m", "cancelled")])

    def test_unknown_cue_type_is_ignored(self):
        doc = {"programs": [program("p", [component("A")], [
            cue("weird", "A", "future-type", at=10),
            cue("ok", "A", "at", at=20),
        ])]}
        cuts = cuts_by_cue(cues.resolve(doc), "p")
        self.assertEqual([c["cue"] for c in cuts], ["ok"])

    def test_equal_version_after_cancel_stays_cancelled(self):
        # Equal versions keep the first occurrence, so the re-sent marker is
        # not a revision and cannot undo the cancel.
        doc = {"programs": [program("p", [component("A")], [
            cue("m", "A", "at", version=1, at=10),
            cue("k", "A", "cancel", target="m"),
            cue("m", "A", "at", version=1, at=50),
        ])]}
        cuts = cuts_by_cue(cues.resolve(doc), "p")
        self.assertEqual(
            [(c["at"], c["state"]) for c in cuts],
            [(10, "cancelled"), (50, "superseded")],
        )

    def test_delayed_marker_is_not_replayed_after_resume_point(self):
        # The cue is listed late (marker delay) but its programme position is
        # before the resume point, so a resume from later must skip it.
        doc = {"programs": [program("p", [component("A")], [
            cue("late", "A", "at", at=5),
            cue("next", "A", "at", at=60),
        ])], "resume": [{"program": "p", "from": 30}]}
        result = cues.resolve(doc)
        full = sorted(c["cue"] for c in cuts_by_cue(result, "p")
                      if c["state"] == "active")
        self.assertEqual(full, ["late", "next"])
        self.assertEqual(result["resume"][0]["cuts"], ["next"])

    def test_cancel_before_target_has_no_effect(self):
        doc = {"programs": [program("p", [component("A")], [
            cue("k", "A", "cancel", target="m"),
            cue("m", "A", "at", at=10),
        ])]}
        cuts = cuts_by_cue(cues.resolve(doc), "p")
        self.assertEqual([(c["cue"], c["state"]) for c in cuts], [("m", "active")])

    def test_duplicate_component_entries_emit_one_cut(self):
        doc = {"programs": [program("p", [
            component("A", 5), component("A", 99),
        ], [
            cue("pc", "ignored", "per-component", at=10),
        ])]}
        cuts = cuts_by_cue(cues.resolve(doc), "p")
        self.assertEqual([(c["component"], c["at"]) for c in cuts], [("A", 15)])

    def test_resume_of_unknown_programme_is_empty(self):
        doc = {"programs": [], "resume": [{"program": "nope", "from": 0}]}
        self.assertEqual(cues.resolve(doc)["resume"],
                         [{"program": "nope", "from": 0, "cuts": []}])


class PropertyTests(unittest.TestCase):
    """Resume must agree with a full scan from the same position."""

    SCENARIOS = [
        {"programs": [program("p", [component("A", 0), component("B", 25)], [
            cue("a", "A", "immediate", at=0),
            cue("b", "B", "at", at=25),
            cue("c", "ignored", "per-component", at=25),
            cue("c", "B", "at", version=2, at=50),
            cue("k", "B", "cancel", target="b"),
            cue("a", "A", "at", version=2, at=75),
        ])]},
        {"programs": [program("p", [component("S", 10)], [
            cue("x", "S", "at", version=2, at=20),
            cue("x", "S", "at", version=1, at=10),
            cue("k", "S", "cancel", version=2, target="x"),
            cue("k", "S", "cancel", version=1, target="x"),
            cue("x", "S", "at", version=3, at=30),
        ])]},
    ]

    def test_resume_suffix_matches_full_scan(self):
        for scenario in self.SCENARIOS:
            document = {"programs": scenario["programs"],
                        "resume": [{"program": "p", "from": point}
                                   for point in range(-5, 90, 5)]}
            result = cues.resolve(document)
            cuts = cuts_by_cue(result, "p")
            for entry in result["resume"]:
                expected = sorted(c["cue"] for c in cuts
                                  if c["state"] == "active"
                                  and c["at"] >= entry["from"])
                self.assertEqual(entry["cuts"], expected)


if __name__ == "__main__":
    unittest.main()
