"""Acceptance tests for replicate.py.

A prefix-by-prefix transaction interpreter (model) is checked against the
CLI for both snapshot generations; crash boundaries are forced with
--crash and the public state is inspected before each resume.
"""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CLI = ROOT / "replicate.py"

sys.path.insert(0, str(ROOT))
from replicate import apply_ops  # noqa: E402


def model(source, n):
    """Reference interpreter: expected visible state after n events."""
    tables = {}
    audit = []
    visible = None
    published = False
    staged = set()
    buf = {}
    for event in source["events"][:n]:
        kind = event["kind"]
        if kind == "stage":
            staged.add(event["chunk"])
        elif kind == "seal":
            if published:
                continue
            assert len(staged) == len(source["chunks"])
            for chunk in source["chunks"]:
                tables.setdefault(chunk["table"], {}).update(
                    copy.deepcopy(chunk["rows"]))
            published = True
            visible = source["snapshot_lsn"]
            ready = sorted((b["lsn"], x) for x, b in buf.items()
                           if b["lsn"] is not None)
            for lsn, xid in ready:
                apply_ops(tables, buf[xid]["ops"])
                audit.append((xid, lsn))
                visible = lsn
            buf = {x: b for x, b in buf.items() if b["lsn"] is None}
        elif kind == "begin":
            buf[event["xid"]] = {"ops": [], "lsn": None}
        elif kind == "op":
            buf[event["xid"]]["ops"].append(event)
        elif kind == "abort":
            buf.pop(event["xid"], None)
        elif kind == "commit":
            pending = buf.pop(event["xid"])
            pair = (event["xid"], event["lsn"])
            if pair in audit or event["lsn"] <= source["snapshot_lsn"]:
                continue
            if published:
                apply_ops(tables, pending["ops"])
                audit.append(pair)
                visible = event["lsn"]
            else:
                pending["lsn"] = event["lsn"]
                buf[event["xid"]] = pending
    return {
        "published": published,
        "tables": tables if published else {},
        "audit": audit if published else [],
        "visible_lsn": visible,
    }


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "replica.db"

    def tearDown(self):
        self.tmp.cleanup()

    def write_source(self, name, source):
        path = self.dir / name
        path.write_text(json.dumps(source))
        return path

    def run_cli(self, *args, expect=0):
        proc = subprocess.run(
            [sys.executable, str(CLI)] + [str(a) for a in args],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, expect,
                         "args=%r stderr=%r" % (args, proc.stderr))
        return proc

    def apply(self, source_path, *extra, expect=0):
        proc = self.run_cli("apply", "--source", source_path,
                            "--db", self.db, *extra, expect=expect)
        if expect == 0:
            return json.loads(proc.stdout)
        return proc

    def inspect(self):
        proc = self.run_cli("inspect", "--db", self.db)
        return json.loads(proc.stdout)

    def check_view(self, view, source, n, snapshot_id=None):
        want = model(source, n)
        self.assertEqual(view["published"], want["published"])
        self.assertEqual(view["tables"], want["tables"])
        got_audit = [(a["xid"], a["lsn"]) for a in view["audit"]]
        self.assertEqual(got_audit, want["audit"])
        self.assertEqual(view["visible_lsn"], want["visible_lsn"])
        self.assertEqual(view["applied_events"], n)
        if not want["published"]:
            self.assertIsNone(view["snapshot_id"])
        elif snapshot_id is not None:
            self.assertEqual(view["snapshot_id"], snapshot_id)


def rich_source():
    return {
        "snapshot_id": "s1",
        "snapshot_lsn": 100,
        "available_from": 1,
        "chunks": [
            {"table": "customer", "rows": {"a": {"tier": 1}, "b": {"tier": 2}}},
            {"table": "orders", "rows": {"o1": {"amt": 5}}},
        ],
        "events": [
            {"kind": "stage", "chunk": 0},
            {"kind": "begin", "xid": "t1"},
            {"kind": "op", "xid": "t1", "table": "customer", "key": "c",
             "action": "set", "value": {"tier": 3}},
            {"kind": "stage", "chunk": 1},
            {"kind": "op", "xid": "t1", "table": "orders", "key": "o2",
             "action": "set", "value": {"amt": 7}},
            {"kind": "begin", "xid": "t2"},
            {"kind": "op", "xid": "t2", "table": "customer", "key": "a",
             "action": "delete"},
            {"kind": "abort", "xid": "t2"},
            {"kind": "commit", "xid": "t1", "lsn": 50},
            {"kind": "begin", "xid": "t3"},
            {"kind": "op", "xid": "t3", "table": "customer", "key": "d",
             "action": "set", "value": {"tier": 4}},
            {"kind": "commit", "xid": "t3", "lsn": 101},
            {"kind": "disconnect"},
            {"kind": "reconnect"},
            {"kind": "begin", "xid": "t4"},
            {"kind": "op", "xid": "t4", "table": "customer", "key": "b",
             "action": "move", "new_key": "b2", "patch": {"tier": 9}},
            {"kind": "seal"},
            {"kind": "begin", "xid": "t5"},
            {"kind": "op", "xid": "t5", "table": "customer", "key": "e",
             "action": "set", "value": {"tier": 5}},
            {"kind": "commit", "xid": "t5", "lsn": 102},
            {"kind": "disconnect"},
            {"kind": "reconnect"},
            {"kind": "begin", "xid": "t5"},
            {"kind": "op", "xid": "t5", "table": "customer", "key": "e",
             "action": "set", "value": {"tier": 5}},
            {"kind": "commit", "xid": "t5", "lsn": 102},
            {"kind": "begin", "xid": "t6"},
            {"kind": "op", "xid": "t6", "table": "customer", "key": "c",
             "action": "delete"},
            {"kind": "commit", "xid": "t6", "lsn": 103},
            {"kind": "commit", "xid": "t4", "lsn": 104},
        ],
    }


def gen_a_source(extra_events=(), available_from=1):
    return {
        "snapshot_id": "ga",
        "snapshot_lsn": 100,
        "available_from": available_from,
        "chunks": [{"table": "customer", "rows": {"a": {"v": 1}}}],
        "events": [
            {"kind": "stage", "chunk": 0},
            {"kind": "seal"},
            {"kind": "begin", "xid": "A1"},
            {"kind": "op", "xid": "A1", "table": "customer", "key": "x",
             "action": "set", "value": {"v": 2}},
            {"kind": "commit", "xid": "A1", "lsn": 101},
        ] + list(extra_events),
    }


def gen_b_source():
    return {
        "snapshot_id": "gb",
        "snapshot_lsn": 200,
        "available_from": 1,
        "chunks": [
            {"table": "customer", "rows": {"m": {"v": 9}}},
            {"table": "orders", "rows": {"o": {"v": 1}}},
        ],
        "events": [
            {"kind": "stage", "chunk": 0},
            {"kind": "stage", "chunk": 1},
            {"kind": "begin", "xid": "B1"},
            {"kind": "op", "xid": "B1", "table": "customer", "key": "n",
             "action": "set", "value": {"v": 8}},
            {"kind": "commit", "xid": "B1", "lsn": 201},
            {"kind": "seal"},
            {"kind": "begin", "xid": "A1"},
            {"kind": "op", "xid": "A1", "table": "customer", "key": "p",
             "action": "set", "value": {"v": 7}},
            {"kind": "commit", "xid": "A1", "lsn": 202},
        ],
    }


class TestBasic(Case):
    def test_demo(self):
        source = json.loads((ROOT / "demo.json").read_text())
        path = self.write_source("demo.json", source)
        out = self.apply(path)
        self.assertTrue(out["published"])
        self.assertEqual(out["tables"], {"customer": {"a": {"tier": 2}}})
        self.assertEqual(out["visible_lsn"], 11)
        self.assertEqual([(a["xid"], a["lsn"]) for a in out["audit"]],
                         [("x", 11)])
        self.assertEqual(out["snapshot_id"], "demo")
        self.assertEqual(self.inspect(), out)

    def test_prefix_interpreter_first_generation(self):
        source = rich_source()
        path = self.write_source("s1.json", source)
        total = len(source["events"])
        for n in range(total + 1):
            out = self.apply(path, "--limit", n)
            self.check_view(out, source, n, snapshot_id="s1")
            self.check_view(self.inspect(), source, n, snapshot_id="s1")
        # Replaying the whole source must not re-apply anything.
        out = self.apply(path)
        self.check_view(out, source, total, snapshot_id="s1")

    def test_prefix_interpreter_second_generation_shape(self):
        # The generation-B feed checked as a standalone generation.
        source = gen_b_source()
        path = self.write_source("gb.json", source)
        total = len(source["events"])
        for n in range(total + 1):
            out = self.apply(path, "--limit", n)
            self.check_view(out, source, n, snapshot_id="gb")


class TestCrashBoundaries(Case):
    def run_crashed(self, source, crash):
        path = self.write_source("s.json", source)
        proc = self.apply(path, "--crash", crash, expect=97)
        self.assertEqual(proc.stdout, "")
        return path

    def test_after_stage(self):
        source = rich_source()
        path = self.run_crashed(source, "after-stage")
        view = self.inspect()
        self.check_view(view, source, 1)
        out = self.apply(path)
        self.check_view(out, source, len(source["events"]), "s1")

    def test_before_publish(self):
        source = rich_source()
        path = self.run_crashed(source, "before-publish")
        view = self.inspect()
        seal = [i for i, e in enumerate(source["events"])
                if e["kind"] == "seal"][0]
        self.check_view(view, source, seal)
        self.assertFalse(view["published"])
        out = self.apply(path)
        self.check_view(out, source, len(source["events"]), "s1")

    def test_before_commit(self):
        source = rich_source()
        path = self.run_crashed(source, "before-commit")
        view = self.inspect()
        # First published-state commit is t5 at index 19.
        self.check_view(view, source, 19, "s1")
        self.assertEqual(view["visible_lsn"], 101)
        out = self.apply(path)
        self.check_view(out, source, len(source["events"]), "s1")

    def test_after_commit(self):
        source = rich_source()
        path = self.run_crashed(source, "after-commit")
        view = self.inspect()
        self.check_view(view, source, 20, "s1")
        self.assertEqual(view["visible_lsn"], 102)
        out = self.apply(path)
        self.check_view(out, source, len(source["events"]), "s1")
        # No duplicate audit entries after resume.
        pairs = [(a["xid"], a["lsn"]) for a in out["audit"]]
        self.assertEqual(len(pairs), len(set(pairs)))


class TestRebuild(Case):
    def test_switch_and_stale_rejection(self):
        a = self.write_source("a.json", gen_a_source())
        b = self.write_source("b.json", gen_b_source())
        out = self.apply(a)
        self.assertEqual(out["visible_lsn"], 101)

        # Start the background rebuild; visible view stays on generation A.
        out = self.apply(b, "--replace", "--limit", 3)
        self.assertEqual(out["snapshot_id"], "ga")
        self.assertEqual(out["visible_lsn"], 101)
        self.assertEqual(out["rebuild"]["snapshot_id"], "gb")
        self.assertEqual(out["rebuild"]["applied_events"], 3)
        self.assertEqual(self.inspect()["snapshot_id"], "ga")

        # Old generation keeps applying its own log while rebuild runs.
        a2 = self.write_source("a2.json", gen_a_source(extra_events=[
            {"kind": "begin", "xid": "A2"},
            {"kind": "op", "xid": "A2", "table": "customer", "key": "a",
             "action": "delete"},
            {"kind": "commit", "xid": "A2", "lsn": 102},
        ]))
        out = self.apply(a2)
        self.assertEqual(out["visible_lsn"], 102)
        self.assertEqual([(x["xid"], x["lsn"]) for x in out["audit"]],
                         [("A1", 101), ("A2", 102)])
        self.assertEqual(out["rebuild"]["applied_events"], 3)

        # Finish the rebuild: seal switches atomically, same xid "A1" is
        # reused by the new generation without confusion.
        out = self.apply(b, "--replace")
        self.assertNotIn("rebuild", out)
        self.assertEqual(out["snapshot_id"], "gb")
        self.assertEqual(out["visible_lsn"], 202)
        self.assertEqual([(x["xid"], x["lsn"]) for x in out["audit"]],
                         [("B1", 201), ("A1", 202)])
        self.assertEqual(out["tables"], {
            "customer": {"m": {"v": 9}, "n": {"v": 8}, "p": {"v": 7}},
            "orders": {"o": {"v": 1}},
        })
        self.assertEqual(self.inspect(), out)

        # Old source is rejected after the switch, with or without
        # --replace, and the published state is not rolled back.
        proc = self.apply(a2, expect=2)
        self.assertIn("resnapshot_required", proc.stderr)
        proc = self.apply(a2, "--replace", expect=2)
        self.assertIn("stale_snapshot", proc.stderr)
        self.assertEqual(self.inspect(), out)

        # Replaying the current generation keeps its own progress.
        again = self.apply(b)
        self.assertEqual(again, out)
        again = self.apply(b, "--replace")
        self.assertEqual(again, out)

    def test_stale_snapshot_at_seal_keeps_old_cursor(self):
        a = self.write_source("a.json", gen_a_source())
        self.apply(a)
        b2 = self.write_source("b2.json", {
            "snapshot_id": "gb",
            "snapshot_lsn": 102,
            "available_from": 1,
            "chunks": [{"table": "customer", "rows": {"z": {"v": 0}}}],
            "events": [
                {"kind": "stage", "chunk": 0},
                {"kind": "seal"},
            ],
        })
        out = self.apply(b2, "--replace", "--limit", 1)
        self.assertEqual(out["rebuild"]["staged_chunks"], 1)

        # Old generation advances past the new snapshot baseline.
        a3 = self.write_source("a3.json", gen_a_source(extra_events=[
            {"kind": "begin", "xid": "A9"},
            {"kind": "op", "xid": "A9", "table": "customer", "key": "y",
             "action": "set", "value": {"v": 5}},
            {"kind": "commit", "xid": "A9", "lsn": 150},
        ]))
        out = self.apply(a3)
        self.assertEqual(out["visible_lsn"], 150)

        # Seal of the stale snapshot fails; nothing is published.
        proc = self.apply(b2, "--replace", expect=2)
        self.assertIn("stale_snapshot", proc.stderr)
        view = self.inspect()
        self.assertEqual(view["snapshot_id"], "ga")
        self.assertEqual(view["visible_lsn"], 150)

        # Old cursor keeps reading its log afterwards.
        a4 = self.write_source("a4.json", gen_a_source(extra_events=[
            {"kind": "begin", "xid": "A9"},
            {"kind": "op", "xid": "A9", "table": "customer", "key": "y",
             "action": "set", "value": {"v": 5}},
            {"kind": "commit", "xid": "A9", "lsn": 150},
            {"kind": "begin", "xid": "A10"},
            {"kind": "op", "xid": "A10", "table": "customer", "key": "w",
             "action": "set", "value": {"v": 6}},
            {"kind": "commit", "xid": "A10", "lsn": 151},
        ]))
        out = self.apply(a4)
        self.assertEqual(out["visible_lsn"], 151)
        self.assertEqual([(x["xid"], x["lsn"]) for x in out["audit"]],
                         [("A1", 101), ("A9", 150), ("A10", 151)])

    def test_crash_after_publish_at_switch(self):
        a = self.write_source("a.json", gen_a_source())
        b = self.write_source("b.json", gen_b_source())
        self.apply(a)
        self.apply(b, "--replace", "--limit", 5)
        proc = self.apply(b, "--replace", "--crash", "after-publish",
                          expect=97)
        self.assertEqual(proc.stdout, "")
        # Switch is durable: new generation visible, post-seal events pending.
        view = self.inspect()
        self.check_view(view, gen_b_source(), 6, "gb")
        out = self.apply(b, "--replace")
        self.check_view(out, gen_b_source(), 9, "gb")

    def test_crash_before_publish_at_switch(self):
        a = self.write_source("a.json", gen_a_source())
        b = self.write_source("b.json", gen_b_source())
        self.apply(a)
        self.apply(b, "--replace", "--limit", 5)
        self.apply(b, "--replace", "--crash", "before-publish", expect=97)
        view = self.inspect()
        self.assertEqual(view["snapshot_id"], "ga")
        self.assertEqual(view["visible_lsn"], 101)
        out = self.apply(b, "--replace")
        self.check_view(out, gen_b_source(), 9, "gb")

    def test_crash_after_stage_during_rebuild(self):
        a = self.write_source("a.json", gen_a_source())
        b = self.write_source("b.json", gen_b_source())
        self.apply(a)
        self.apply(b, "--replace", "--crash", "after-stage", expect=97)
        view = self.inspect()
        self.assertEqual(view["snapshot_id"], "ga")
        out = self.apply(b, "--replace", "--limit", 2)
        self.assertEqual(out["rebuild"]["applied_events"], 2)
        self.assertEqual(out["snapshot_id"], "ga")
        out = self.apply(b, "--replace")
        self.check_view(out, gen_b_source(), 9, "gb")

    def test_crash_commit_boundaries_on_old_generation(self):
        a = self.write_source("a.json", gen_a_source())
        b = self.write_source("b.json", gen_b_source())
        self.apply(a)
        self.apply(b, "--replace", "--limit", 2)
        a2 = self.write_source("a2.json", gen_a_source(extra_events=[
            {"kind": "begin", "xid": "A2"},
            {"kind": "op", "xid": "A2", "table": "customer", "key": "q",
             "action": "set", "value": {"v": 3}},
            {"kind": "commit", "xid": "A2", "lsn": 102},
        ]))
        for crash, cursor, visible in (
                ("before-commit", 7, 101), ("after-commit", 8, 102)):
            self.apply(a2, "--crash", crash, expect=97)
            view = self.inspect()
            self.assertEqual(view["snapshot_id"], "ga")
            self.assertEqual(view["applied_events"], cursor)
            self.assertEqual(view["visible_lsn"], visible)
            if crash == "before-commit":
                out = self.apply(a2)
                self.assertEqual([(x["xid"], x["lsn"]) for x in view["audit"]],
                                 [("A1", 101)])
                self.assertEqual(out["visible_lsn"], 102)
            else:
                out = self.apply(a2)
            pairs = [(x["xid"], x["lsn"]) for x in out["audit"]]
            self.assertEqual(pairs, [("A1", 101), ("A2", 102)])
            # Reset for the second boundary.
            self.setUp()
            a = self.write_source("a.json", gen_a_source())
            b = self.write_source("b.json", gen_b_source())
            self.apply(a)
            self.apply(b, "--replace", "--limit", 2)
            a2 = self.write_source("a2.json", gen_a_source(extra_events=[
                {"kind": "begin", "xid": "A2"},
                {"kind": "op", "xid": "A2", "table": "customer", "key": "q",
                 "action": "set", "value": {"v": 3}},
                {"kind": "commit", "xid": "A2", "lsn": 102},
            ]))
        # Rebuild is still intact and can finish.
        out = self.apply(b, "--replace")
        self.check_view(out, gen_b_source(), 9, "gb")


class TestResnapshotRequired(Case):
    def test_snapshot_id_mismatch(self):
        a = self.write_source("a.json", gen_a_source())
        self.apply(a)
        other = self.write_source("other.json", {
            "snapshot_id": "gz",
            "snapshot_lsn": 500,
            "available_from": 1,
            "chunks": [],
            "events": [{"kind": "seal"}],
        })
        proc = self.apply(other, expect=2)
        self.assertIn("resnapshot_required", proc.stderr)
        view = self.inspect()
        self.assertEqual(view["snapshot_id"], "ga")
        self.assertEqual(view["visible_lsn"], 101)

    def test_available_from_beyond_visible(self):
        a = self.write_source("a.json", gen_a_source())
        self.apply(a)
        recycled = self.write_source("recycled.json",
                                     gen_a_source(available_from=500))
        proc = self.apply(recycled, expect=2)
        self.assertIn("resnapshot_required", proc.stderr)
        self.assertEqual(self.inspect()["visible_lsn"], 101)

    def test_replace_without_published_result(self):
        # --replace on a fresh db behaves like a normal first apply.
        b = self.write_source("b.json", gen_b_source())
        out = self.apply(b, "--replace")
        self.check_view(out, gen_b_source(), 9, "gb")


if __name__ == "__main__":
    unittest.main()
