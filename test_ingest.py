#!/usr/bin/env python3
"""Acceptance tests for the columnar event importer.

The suite uses ``oracle.py`` (an independent per-event interpreter written
from the contract) to derive the expected parent/child/reject tables, then
checks them against the Parquet output of the real CLI.  Run with::

    python3 -m unittest -v test_ingest
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pyarrow.parquet as pq

import oracle
from ingest import EVENTS_SCHEMA, READINGS_SCHEMA, REJECTS_SCHEMA

HERE = Path(__file__).resolve().parent
CLI = HERE / "ingest.py"

EVENT_FIELDS = ["id", "rev", "device", "array_state"]
CHILD_FIELDS = ["id", "rev", "ordinal", "channel", "value"]
REJECT_FIELDS = ["id", "rev", "source", "line", "path", "reason"]


def run_cli(*args, expect=0):
    env = dict(os.environ)
    # PyArrow's CPU probe prints harmless warnings in some sandboxes.
    env.setdefault("ARROW_IO_THREADS", "1")
    proc = subprocess.run(
        [sys.executable, str(CLI)] + [str(a) for a in args],
        capture_output=True,
        text=True,
        env=env,
    )
    if expect is not None and proc.returncode != expect:
        raise AssertionError(
            "cli %r exited %d, expected %d\nstdout=%s\nstderr=%s"
            % (args, proc.returncode, expect, proc.stdout, proc.stderr)
        )
    return proc


def inspect(root, version=None):
    args = [root, "inspect"]
    if version is not None:
        args += ["--version", version]
    proc = run_cli(*args)
    return json.loads(proc.stdout)


def write_ndjson(path, events, raw_lines=None):
    lines = []
    for event in events:
        lines.append(json.dumps(event, separators=(",", ":")))
    if raw_lines:
        lines.extend(raw_lines)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def read_version(root, version, name):
    files = inspect(root, version)["files"]
    path = Path(root) / files[name]
    return pq.read_table(path).to_pylist()


def rows(table, fields):
    return [tuple(sorted((f, row[f]) for f in fields)) for row in table]


def load_file(path):
    with open(path) as handle:
        return [json.loads(line) for line in handle if line.strip()]


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ingest-test-"))
        self.root = self.tmp / "store"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ingest(self, events, name="batch.ndjson", crash=False, expect=0):
        path = self.tmp / name
        write_ndjson(path, events)
        args = [self.root, "ingest", path]
        if crash:
            args.append("--crash-before-publish")
        proc = run_cli(*args, expect=expect)
        return path, proc

    def assert_matches_oracle(self, batches, version=None):
        """Compare all three public tables with the oracle replay."""

        expected_events, expected_children, expected_rejects = oracle.replay(
            batches
        )
        actual_version = inspect(self.root)["version"] if version is None else version

        actual_events = read_version(self.root, actual_version, "events")
        actual_children = read_version(self.root, actual_version, "readings")
        actual_rejects = read_version(self.root, actual_version, "rejects")

        self.assertEqual(
            sorted(rows(actual_events, EVENT_FIELDS)),
            sorted(rows(expected_events, EVENT_FIELDS)),
            "events mismatch",
        )
        self.assertEqual(
            sorted(rows(actual_children, CHILD_FIELDS)),
            sorted(rows(expected_children, CHILD_FIELDS)),
            "readings mismatch",
        )
        self.assertEqual(
            sorted(rows(actual_rejects, REJECT_FIELDS)),
            sorted(rows(expected_rejects, REJECT_FIELDS)),
            "rejects mismatch",
        )
        return actual_version


class SchemaAndPublishTests(StoreTestCase):
    def test_empty_store_is_version_zero_with_real_files(self):
        info = inspect(self.root)
        self.assertEqual(info["version"], 0)
        for name in ("events", "readings", "rejects"):
            relative = info["files"][name]
            self.assertTrue(relative.endswith("/%s.parquet" % name))
            path = Path(self.root) / relative
            self.assertTrue(path.is_file(), path)
            self.assertEqual(pq.read_table(path).num_rows, 0)

    def test_parent_and_child_columns(self):
        events = [
            {"id": "b", "rev": 1, "schema": 2, "device": "x",
             "samples": [{"ch": "c1", "v": 5}, {"ch": "c2", "v": -7}]},
            {"id": "a", "rev": 1, "schema": 1, "device": "x",
             "readings": [{"channel": "temp", "value": 3}]},
        ]
        self.ingest(events)
        events_table = read_version(self.root, 1, "events")
        readings_table = read_version(self.root, 1, "readings")
        self.assertEqual(
            [row["id"] for row in events_table], ["a", "b"]
        )
        self.assertEqual(
            [(row["id"], row["ordinal"], row["channel"], row["value"])
             for row in readings_table],
            [("a", 0, "temp", 3), ("b", 0, "c1", 5), ("b", 1, "c2", -7)],
        )
        # Stable event ids and source order.
        self.assertEqual(
            pq.read_table(Path(self.root) / inspect(self.root)["files"]["readings"]).schema.names,
            CHILD_FIELDS,
        )


class ArrayStateTests(StoreTestCase):
    def test_all_array_states_are_preserved(self):
        events = [
            {"id": "missing", "rev": 1, "schema": 1, "device": "d"},
            {"id": "null", "rev": 1, "schema": 2, "device": "d", "samples": None},
            {"id": "empty", "rev": 1, "schema": 1, "device": "d", "readings": []},
            {"id": "present", "rev": 1, "schema": 2, "device": "d",
             "samples": [{"ch": "a", "v": 1}]},
        ]
        path, _ = self.ingest(events)
        self.assert_matches_oracle([(str(path), events)])

        states = {
            row["id"]: row["array_state"]
            for row in read_version(self.root, 1, "events")
        }
        self.assertEqual(
            states,
            {"missing": "missing", "null": "null",
             "empty": "empty", "present": "present"},
        )
        # Empty/null/missing arrays must not create an empty child row.
        self.assertEqual(
            [row["id"] for row in read_version(self.root, 1, "readings")],
            ["present"],
        )

    def test_multichannel_order_and_duplicate_channels(self):
        events = [
            {"id": "m", "rev": 1, "schema": 2, "device": "d",
             "samples": [{"ch": "z", "v": 9}, {"ch": "a", "v": 1},
                         {"ch": "z", "v": 2}]},
            {"id": "n", "rev": 1, "schema": 1, "device": "d",
             "readings": [{"channel": "c", "value": 4},
                          {"channel": "b", "value": 5},
                          {"channel": "a", "value": 6}]},
        ]
        path, _ = self.ingest(events)
        self.assert_matches_oracle([(str(path), events)])
        children = [
            (row["id"], row["ordinal"], row["channel"], row["value"])
            for row in read_version(self.root, 1, "readings")
        ]
        self.assertEqual(
            children,
            [("m", 0, "z", 9), ("m", 1, "a", 1), ("m", 2, "z", 2),
             ("n", 0, "c", 4), ("n", 1, "b", 5), ("n", 2, "a", 6)],
        )

    def test_schema_version_determines_child_key_names(self):
        events = [
            {"id": "s1", "rev": 1, "schema": 1, "device": "d",
             "readings": [{"channel": "k", "value": 1}]},
            {"id": "s2", "rev": 1, "schema": 2, "device": "d",
             "samples": [{"ch": "k", "v": 2}]},
        ]
        path, _ = self.ingest(events)
        self.assert_matches_oracle([(str(path), events)])


class RejectContractTests(StoreTestCase):
    BAD_EVENTS = [
        {"id": "null_elem", "rev": 1, "schema": 2, "device": "d",
         "samples": [{"ch": "ok", "v": 1}, None]},
        {"id": "bad_value", "rev": 1, "schema": 2, "device": "d",
         "samples": [{"ch": "ok", "v": "1"}]},
        {"id": "bool_value", "rev": 1, "schema": 1, "device": "d",
         "readings": [{"channel": "ok", "value": True}]},
        {"id": "no_channel", "rev": 1, "schema": 1, "device": "d",
         "readings": [{"value": 1}]},
        {"id": "empty_channel", "rev": 1, "schema": 2, "device": "d",
         "samples": [{"ch": "", "v": 1}]},
        {"id": "non_list", "rev": 1, "schema": 1, "device": "d",
         "readings": {"channel": "ok", "value": 1}},
        {"id": "bad_schema", "rev": 1, "schema": 3, "device": "d"},
        {"id": "bad_rev", "rev": 0, "schema": 1, "device": "d"},
    ]

    def test_one_bad_child_rejects_the_whole_event(self):
        path, _ = self.ingest(self.BAD_EVENTS)
        self.assert_matches_oracle([(str(path), self.BAD_EVENTS)])
        events = read_version(self.root, 1, "events")
        readings = read_version(self.root, 1, "readings")
        rejects = read_version(self.root, 1, "rejects")
        self.assertEqual(events, [])
        self.assertEqual(readings, [])
        self.assertEqual(len(rejects), len(self.BAD_EVENTS))
        # Each rejection traces back to its source line and JSON pointer.
        by_id = {row["id"]: row for row in rejects}
        self.assertEqual(by_id["null_elem"]["path"], "/samples/1")
        self.assertEqual(by_id["null_elem"]["line"], 1)
        self.assertEqual(by_id["bad_value"]["path"], "/samples/0/v")
        self.assertEqual(by_id["bool_value"]["path"], "/readings/0/value")
        self.assertEqual(by_id["no_channel"]["path"], "/readings/0/channel")
        self.assertEqual(by_id["empty_channel"]["path"], "/samples/0/ch")
        self.assertEqual(by_id["non_list"]["path"], "/readings")
        self.assertEqual(by_id["bad_schema"]["path"], "/schema")
        self.assertEqual(by_id["bad_rev"]["path"], "/rev")

    def test_invalid_json_line_is_rejected_with_offset(self):
        path = self.tmp / "raw.ndjson"
        path.write_text(
            '{"id":"good","rev":1,"schema":1,"device":"d"}\n'
            'not json\n',
            encoding="utf-8",
        )
        run_cli(self.root, "ingest", path)
        rejects = read_version(self.root, 1, "rejects")
        self.assertEqual(len(rejects), 1)
        self.assertEqual(rejects[0]["line"], 2)
        self.assertEqual(rejects[0]["path"], "/")
        self.assertTrue(rejects[0]["reason"].startswith("invalid JSON"))
        self.assertEqual(rejects[0]["source"], str(path))

    def test_reject_rows_are_idempotent(self):
        path, _ = self.ingest(self.BAD_EVENTS, name="bad.ndjson")
        first = inspect(self.root)["version"]
        run_cli(self.root, "ingest", path)
        second = inspect(self.root)["version"]
        self.assertEqual(first, second, "a replay must not publish")
        self.assertEqual(len(read_version(self.root, second, "rejects")),
                         len(self.BAD_EVENTS))

    def test_invalid_new_revision_keeps_previous_accepted_version(self):
        good = [{"id": "x", "rev": 1, "schema": 1, "device": "d",
                 "readings": [{"channel": "c", "value": 7}]}]
        bad = [{"id": "x", "rev": 2, "schema": 1, "device": "d",
                "readings": [{"channel": "c", "value": 7}, None]}]
        path1, _ = self.ingest(good, name="good.ndjson")
        path2, _ = self.ingest(bad, name="bad.ndjson")

        self.assert_matches_oracle(
            [(str(path1), good), (str(path2), bad)]
        )
        events = read_version(self.root, 2, "events")
        readings = read_version(self.root, 2, "readings")
        self.assertEqual([(row["id"], row["rev"]) for row in events],
                         [("x", 1)])
        self.assertEqual(
            [(row["id"], row["rev"], row["value"]) for row in readings],
            [("x", 1, 7)],
        )


class VersioningTests(StoreTestCase):
    def test_higher_revision_replaces_parent_and_children(self):
        batch1 = [
            {"id": "a", "rev": 1, "schema": 2, "device": "d",
             "samples": [{"ch": "x", "v": 1}, {"ch": "y", "v": 2}]},
        ]
        batch2 = [
            {"id": "a", "rev": 3, "schema": 2, "device": "d",
             "samples": [{"ch": "z", "v": 3}]},
        ]
        path1, _ = self.ingest(batch1, name="b1.ndjson")
        path2, _ = self.ingest(batch2, name="b2.ndjson")
        self.assert_matches_oracle(
            [(str(path1), batch1), (str(path2), batch2)]
        )
        children = read_version(self.root, 2, "readings")
        self.assertEqual(
            [(row["rev"], row["channel"], row["value"]) for row in children],
            [(3, "z", 3)],
        )
        # v1 is still readable and self-consistent.
        self.assert_no_orphans(self.root, 1)
        self.assert_no_orphans(self.root, 2)

    def test_lower_revision_after_newer_is_ignored(self):
        newer = [{"id": "a", "rev": 5, "schema": 1, "device": "d",
                  "readings": [{"channel": "c", "value": 5}]}]
        older = [{"id": "a", "rev": 4, "schema": 1, "device": "d",
                  "readings": [{"channel": "c", "value": 4}]}]
        path1, _ = self.ingest(newer, name="n.ndjson")
        path2, _ = self.ingest(older, name="o.ndjson")
        # The stale revision is not a public change, so no new version.
        self.assertEqual(inspect(self.root)["version"], 1)
        self.assert_matches_oracle([(str(path1), newer), (str(path2), older)])
        rows = read_version(self.root, 1, "readings")
        self.assertEqual([(r["rev"], r["value"]) for r in rows], [(5, 5)])

    def test_same_rev_different_payload_is_rejected_and_original_kept(self):
        original = [{"id": "a", "rev": 1, "schema": 2, "device": "d",
                     "samples": [{"ch": "x", "v": 1}]}]
        conflict = [{"id": "a", "rev": 1, "schema": 2, "device": "d",
                     "samples": [{"ch": "x", "v": 999}]}]
        path1, _ = self.ingest(original, name="orig.ndjson")
        path2, _ = self.ingest(conflict, name="conflict.ndjson")
        self.assert_matches_oracle(
            [(str(path1), original), (str(path2), conflict)]
        )
        self.assertEqual(
            [(r["rev"], r["value"]) for r in read_version(self.root, 2, "readings")],
            [(1, 1)],
        )
        rejects = read_version(self.root, 2, "rejects")
        self.assertEqual(len(rejects), 1)
        self.assertEqual(rejects[0]["reason"],
                         "same id/rev with different payload")

    def test_exact_replay_is_a_no_op(self):
        batch = [{"id": "a", "rev": 1, "schema": 1, "device": "d",
                  "readings": [{"channel": "x", "value": 1}]}]
        path, _ = self.ingest(batch, name="same.ndjson")
        run_cli(self.root, "ingest", path)
        self.assertEqual(inspect(self.root)["version"], 1)

    def test_old_versions_remain_inspectable(self):
        batches = []
        for rev in (1, 2, 3):
            batch = [{"id": "a", "rev": rev, "schema": 1, "device": "d",
                      "readings": [{"channel": "c", "value": rev}]}]
            path, _ = self.ingest(batch, name="r%d.ndjson" % rev)
            batches.append((str(path), batch))
        self.assertEqual(inspect(self.root)["version"], 3)
        # Each historical version matches the oracle replay up to that point.
        for version in (1, 2, 3):
            self.assert_matches_oracle(batches[:version], version=version)
            self.assert_no_orphans(self.root, version)
            values = [r["value"] for r in read_version(self.root, version, "readings")]
            self.assertEqual(values, [version])

    def test_rejects_accumulate_across_versions(self):
        bad1 = [{"id": "a", "rev": 1, "schema": 2, "device": "d",
                 "samples": [None]}]
        bad2 = [{"id": "b", "rev": 1, "schema": 2, "device": "d",
                 "samples": [{"ch": "x", "v": 1}, None]}]
        path1, _ = self.ingest(bad1, name="bad1.ndjson")
        path2, _ = self.ingest(bad2, name="bad2.ndjson")
        self.assert_matches_oracle(
            [(str(path1), bad1), (str(path2), bad2)]
        )
        rejects = read_version(self.root, 2, "rejects")
        self.assertEqual({(r["id"], r["line"]) for r in rejects},
                         {("a", 1), ("b", 1)})

    def assert_no_orphans(self, root, version):
        events = read_version(root, version, "events")
        readings = read_version(root, version, "readings")
        parent_ids = {(row["id"], row["rev"]) for row in events}
        self.assertEqual(len(parent_ids), len(events), "duplicate parents")
        for child in readings:
            self.assertIn(
                (child["id"], child["rev"]), parent_ids,
                "orphan child in v%d: %r" % (version, child),
            )
        # Ordinals are dense and unique per parent.
        grouped = {}
        for child in readings:
            grouped.setdefault((child["id"], child["rev"]), []).append(
                child["ordinal"]
            )
        for key, ordinals in grouped.items():
            self.assertEqual(sorted(ordinals), list(range(len(ordinals))), key)


class CrashRecoveryTests(StoreTestCase):
    """Acceptance criterion 2: crash after staging, then backfill."

    Each test interrupts after every table has been written but before the
    version is published, then verifies that recovery backfills exactly the
    corrected events, keeps every version self-consistent with no orphan
    children, and does not duplicate detail rows on repeated input.
    """

    BASE = [
        {"id": "a", "rev": 1, "schema": 1, "device": "d",
         "readings": [{"channel": "humidity", "value": 20}]},
        {"id": "b", "rev": 1, "schema": 2, "device": "d",
         "samples": [{"ch": "x", "v": 1}, {"ch": "y", "v": 2}]},
        {"id": "c", "rev": 1, "schema": 2, "device": "d", "samples": []},
        {"id": "bad", "rev": 1, "schema": 2, "device": "d",
         "samples": [{"ch": "ok", "v": 1}, None]},
    ]

    def test_crash_leaves_no_public_version(self):
        path, _ = self.ingest(self.BASE, name="base.ndjson")
        before = inspect(self.root)["version"]
        self.assertEqual(before, 1)

        crash_batch = [
            {"id": "a", "rev": 2, "schema": 1, "device": "d",
             "readings": [{"channel": "humidity", "value": 21}]},
            {"id": "d", "rev": 1, "schema": 1, "device": "d",
             "readings": [{"channel": "new", "value": 9}]},
        ]
        crash_path = write_ndjson(self.tmp / "crash.ndjson", crash_batch)
        proc = run_cli(self.root, "ingest", crash_path,
                       "--crash-before-publish", expect=97)

        # Exit code 97 and no published version after the crash.
        self.assertEqual(proc.returncode, 97)
        self.assertEqual(inspect(self.root)["version"], before)
        # The staged tables exist but are not referenced by inspect.
        staged = list((Path(self.root) / ".staging").glob("*/events.parquet"))
        self.assertEqual(len(staged), 1)

    def test_recovery_backfills_two_corrections_and_keeps_versions(self):
        base_path, _ = self.ingest(self.BASE, name="base.ndjson")

        # A batch is interrupted after all tables are staged but before the
        # version becomes public.
        crash_batch = [
            {"id": "a", "rev": 2, "schema": 1, "device": "d",
             "readings": [{"channel": "humidity", "value": 21}]},
            {"id": "b", "rev": 2, "schema": 2, "device": "d",
             "samples": [{"ch": "x", "v": 10}, {"ch": "y", "v": 20},
                         {"ch": "z", "v": 30}]},
            {"id": "e", "rev": 1, "schema": 1, "device": "d",
             "readings": [{"channel": "extra", "value": 5}]},
        ]
        crash_path = write_ndjson(self.tmp / "crash.ndjson", crash_batch)
        run_cli(self.root, "ingest", crash_path,
                "--crash-before-publish", expect=97)
        self.assertEqual(inspect(self.root)["version"], 1)

        # Two corrected events are then provided.  Because the interrupted
        # snapshot was rolled back (never public), the corrections are
        # accepted as the first accepted payloads at these identities.
        corrections = [
            {"id": "a", "rev": 2, "schema": 1, "device": "d",
             "readings": [{"channel": "humidity", "value": 22}]},
            {"id": "b", "rev": 2, "schema": 2, "device": "d",
             "samples": [{"ch": "x", "v": 10}, {"ch": "y", "v": 21}]},
        ]
        fix_path = write_ndjson(self.tmp / "fix.ndjson", corrections)
        run_cli(self.root, "ingest", fix_path)

        version = inspect(self.root)["version"]
        self.assertEqual(version, 2)

        # v1 and v2 are both internally consistent (no orphan children).
        self.assert_no_orphans(self.root, 1)
        self.assert_no_orphans(self.root, 2)

        # Backfilled values are visible with exactly one child row each.
        events = {
            row["id"]: row for row in read_version(self.root, 2, "events")
        }
        self.assertEqual(events["a"]["rev"], 2)
        self.assertEqual(events["b"]["rev"], 2)
        # The interrupted batch was never published, so its unique event is
        # absent from the recovered version.
        self.assertNotIn("e", events)
        # The rejected event from the base batch is still there, exactly once.
        self.assertIn("bad", {
            row["id"] for row in read_version(self.root, 2, "rejects")
        })
        children_a = [
            r for r in read_version(self.root, 2, "readings") if r["id"] == "a"
        ]
        children_b = [
            r for r in read_version(self.root, 2, "readings") if r["id"] == "b"
        ]
        self.assertEqual(
            [(r["ordinal"], r["channel"], r["value"]) for r in children_a],
            [(0, "humidity", 22)],
        )
        self.assertEqual(
            [(r["ordinal"], r["channel"], r["value"]) for r in children_b],
            [(0, "x", 10), (1, "y", 21)],
        )
        self.assertEqual(
            len(read_version(self.root, 2, "rejects")), 1,
            "reject rows must not be duplicated",
        )

        # The final state equals an independent replay of base + corrections.
        self.assert_matches_oracle(
            [(str(base_path), self.BASE), (str(fix_path), corrections)],
            version=2,
        )

    def test_repeated_input_after_recovery_adds_no_detail(self):
        base_path, _ = self.ingest(self.BASE, name="base.ndjson")
        crash_batch = [
            {"id": "a", "rev": 2, "schema": 1, "device": "d",
             "readings": [{"channel": "humidity", "value": 21}]},
        ]
        crash_path = write_ndjson(self.tmp / "crash.ndjson", crash_batch)
        run_cli(self.root, "ingest", crash_path,
                "--crash-before-publish", expect=97)

        fix = [{"id": "a", "rev": 2, "schema": 1, "device": "d",
                "readings": [{"channel": "humidity", "value": 22}]}]
        fix_path = write_ndjson(self.tmp / "fix.ndjson", fix)
        run_cli(self.root, "ingest", fix_path)
        version = inspect(self.root)["version"]

        # Ingest the corrections and the base again several times.
        for source in (fix_path, base_path, fix_path, crash_path):
            run_cli(self.root, "ingest", source)

        self.assertGreaterEqual(inspect(self.root)["version"], version)
        latest = inspect(self.root)["version"]
        self.assert_no_orphans(self.root, latest)
        readings = read_version(self.root, latest, "readings")
        a_rows = [r for r in readings if r["id"] == "a"]
        self.assertEqual(len(a_rows), 1)
        self.assertEqual(a_rows[0]["value"], 22)
        # Ids are unique in the parent table.
        parents = read_version(self.root, latest, "events")
        self.assertEqual(len(parents), len({row["id"] for row in parents}))

    def test_recovery_from_first_ever_ingest(self):
        batch = self.BASE
        path = write_ndjson(self.tmp / "first.ndjson", batch)
        run_cli(self.root, "ingest", path,
                "--crash-before-publish", expect=97)
        self.assertEqual(inspect(self.root)["version"], 0)
        run_cli(self.root, "ingest", path)
        self.assertEqual(inspect(self.root)["version"], 1)
        self.assert_no_orphans(self.root, 1)
        self.assert_matches_oracle([(str(path), batch)], version=1)

    def assert_no_orphans(self, root, version):
        events = read_version(root, version, "events")
        readings = read_version(root, version, "readings")
        parent_ids = {(row["id"], row["rev"]) for row in events}
        self.assertEqual(len(parent_ids), len(events), "duplicate parents")
        for child in readings:
            self.assertIn(
                (child["id"], child["rev"]), parent_ids,
                "orphan child in v%d: %r" % (version, child),
            )
        grouped = {}
        for child in readings:
            grouped.setdefault((child["id"], child["rev"]), []).append(
                child["ordinal"]
            )
        for key, ordinals in grouped.items():
            self.assertEqual(sorted(ordinals), list(range(len(ordinals))), key)


class DifferentialTests(StoreTestCase):
    """Randomized differential test against the independent oracle."""

    def _random_event(self, rng):
        event_id = rng.choice(["a", "b", "c", "d", "e"])
        rev = rng.choice([1, 1, 2, 3])
        schema = rng.choice([1, 2])
        event = {"id": event_id, "rev": rev, "schema": schema,
                 "device": rng.choice(["d1", "d2"])}
        field = "readings" if schema == 1 else "samples"
        channel_key = "channel" if schema == 1 else "ch"
        value_key = "value" if schema == 1 else "v"
        choice = rng.randint(0, 6)
        if choice == 0:
            pass  # missing
        elif choice == 1:
            event[field] = None
        elif choice == 2:
            event[field] = []
        elif choice == 3:
            event[field] = [None]
        elif choice == 4:
            event[field] = [{channel_key: "x", value_key: rng.randint(-5, 5)}]
        elif choice == 5:
            event[field] = [
                {channel_key: "x", value_key: rng.randint(-5, 5)},
                {channel_key: "y", value_key: rng.randint(-5, 5)},
                {channel_key: "x", value_key: rng.randint(-5, 5)},
            ]
        else:
            event[field] = [{channel_key: "", value_key: 1}]
        return event

    def test_random_batches_match_oracle(self):
        import random

        for seed in range(8):
            rng = random.Random(seed)
            with self.subTest(seed=seed):
                self.setUp()
                batches = []
                try:
                    for batch_index in range(3):
                        batch = [self._random_event(rng)
                                 for _ in range(rng.randint(1, 6))]
                        path = self.tmp / ("batch%d.ndjson" % batch_index)
                        write_ndjson(path, batch)
                        run_cli(self.root, "ingest", path)
                        batches.append((str(path), batch))
                    version = inspect(self.root)["version"]
                    self.assert_matches_oracle(batches, version=version)
                    self._assert_consistent(version)
                finally:
                    self.tearDown()

    def _assert_consistent(self, version):
        events = read_version(self.root, version, "events")
        readings = read_version(self.root, version, "readings")
        ids = [row["id"] for row in events]
        self.assertEqual(len(ids), len(set(ids)))
        parents = {(row["id"], row["rev"]) for row in events}
        for child in readings:
            self.assertIn((child["id"], child["rev"]), parents)
        for row in events:
            self.assertIn(row["array_state"],
                          ("missing", "null", "empty", "present"))


if __name__ == "__main__":
    unittest.main()