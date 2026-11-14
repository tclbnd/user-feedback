"""Acceptance harness for the manifest compaction protocol.

Two independent checks are performed entirely through the public CLI:

1. ``test_twenty_fixed_interleavings`` -- 20 deterministic interleavings of
   two compaction tasks against concurrent appends and deletes.  An
   independent serial row-level oracle predicts the outcome of every step;
   after each step every published snapshot is compared to the oracle so an
   old reader pinned to any version always observes its original row set.

2. ``test_crash_and_recovery`` -- crashes injected while writing the new
   object, after publishing the pointer, and before GC unlinks anything.
   Each retained manifest is walked to prove reference integrity, and the
   head snapshot's row ids / delete state match the serial truth.

Runs with the standard library only:  ``python3 test_lake.py``
(or ``python3 -m unittest test_lake``).
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAKE = HERE / "lake.py"
CRASH_EXIT = 97


# --------------------------------------------------------------------- CLI
def run_cli(root, *args, expect_exit=0):
    proc = subprocess.run(
        [sys.executable, str(LAKE), str(root), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != expect_exit:
        raise AssertionError(
            "CLI %s exited %d (expected %d)\nstdout=%s\nstderr=%s"
            % (" ".join(args), proc.returncode, expect_exit,
               proc.stdout, proc.stderr))
    if expect_exit == 0 and proc.stdout.strip():
        return json.loads(proc.stdout)
    return None


def write_input(tmpdir, name, payload):
    path = Path(tmpdir) / name
    path.write_text(json.dumps(payload))
    return path


# ----------------------------------------------------------- serial oracle
class SerialOracle:
    """Row-level truth maintained independently of lake.py internals."""

    def __init__(self):
        # Immutable logical file ids: neither appends nor compactions ever
        # mutate a previously published file's row list, mirroring the
        # content-addressed objects in the real lake.
        self.files = []
        self.rows_of = {}
        self.deleted = set()
        self.plans = {}
        # version -> (files tuple, frozenset(deleted))
        self.versions = {0: ((), frozenset())}
        self.head = 0
        self.file_seq = 0

    def _new_file_id(self, tag):
        self.file_seq += 1
        return "f%d-%s" % (self.file_seq, tag)

    def _snapshot(self):
        return (tuple(self.files), frozenset(self.deleted))

    def _record(self):
        self.head += 1
        self.versions[self.head] = self._snapshot()

    def append(self, rows):
        fid = self._new_file_id("append")
        self.files.append(fid)
        self.rows_of[fid] = [dict(r) for r in rows]
        self._record()

    def delete(self, ids):
        self.deleted.update(ids)
        self._record()

    def prepare(self, name):
        selected = tuple(self.files[:2])
        assert len(selected) == 2
        # Same name returns the same frozen plan.
        if name not in self.plans:
            self.plans[name] = {
                "base": self.head,
                "files": selected,
                "deleted_view": frozenset(self.deleted),
                "selected_rows": frozenset(
                    r["id"] for f in selected for r in self.rows_of[f]),
                "outcome": None,
            }
        return self.plans[name]

    def commit(self, name):
        plan = self.plans[name]
        if plan["outcome"] is not None:
            return plan["outcome"]
        if any(f not in self.files for f in plan["files"]):
            outcome = {"status": "conflict", "reason": "selected-file-replaced"}
        elif (self.deleted - plan["deleted_view"]) & plan["selected_rows"]:
            outcome = {"status": "conflict", "reason": "conflicting-delete"}
        else:
            first, second = plan["files"]
            merged_rows = [dict(r) for f in plan["files"]
                           for r in self.rows_of[f]
                           if r["id"] not in self.deleted]
            new_fid = self._new_file_id("merged-" + name)
            self.rows_of[new_fid] = merged_rows
            new_files = []
            for f in self.files:
                if f == second:
                    continue
                new_files.append(new_fid if f == first else f)
            self.files = new_files
            self._record()
            outcome = {"status": "committed", "version": self.head}
        plan["outcome"] = outcome
        return outcome

    def visible_rows(self, version):
        files, deleted = self.versions[version]
        rows = [dict(r) for f in files for r in self.rows_of[f]
                if r["id"] not in deleted]
        return sorted(rows, key=lambda r: r["id"])

    def visible_ids(self, version):
        return [r["id"] for r in self.visible_rows(version)]

    def deleted_at(self, version):
        return sorted(self.versions[version][1])


# -------------------------------------------------------------- fixtures
INITIAL_FILES = [
    [{"id": "r1", "v": 1}, {"id": "r2", "v": 2}],
    [{"id": "r3", "v": 3}, {"id": "r4", "v": 4}],
    [{"id": "r5", "v": 5}, {"id": "r6", "v": 6}],
]
APPEND_X = [{"id": "x1", "v": 101}, {"id": "x2", "v": 102}]
APPEND_Y = [{"id": "y1", "v": 201}, {"id": "y2", "v": 202}]
# z1 lands in a later (non-selected) file; deleting it must be rebase-safe.
APPEND_Z = [{"id": "z1", "v": 301}]
DELETE_SELECTED = ["r2"]          # conflicts with a prepared plan
DELETE_OTHER = ["z1"]             # safe for plans selecting the first 2 files


def bootstrap(root, tmpdir):
    """Create three data files via real appends; return the oracle."""
    oracle = SerialOracle()
    for rows in INITIAL_FILES:
        inp = write_input(tmpdir, "init-%s.json" % rows[0]["id"], rows)
        result = run_cli(root, "append", str(inp))
        oracle.append(rows)
        assert result["version"] == oracle.head
    return oracle


# Fixed 20 interleavings: deterministic shuffles of one fixed operation pool,
# generated with a seeded LCG so the suite is reproducible without any
# third-party dependency.
def fixed_interleavings(count=20):
    base = [
        ("prepare", "p1"),
        ("prepare", "p2"),
        ("commit", "p1"),
        ("commit", "p2"),
        ("append", "X"),
        ("append", "Y"),
        ("append", "Z"),
        ("delete", "selected"),
        ("delete", "other"),
    ]
    sequences = []
    for seed in range(count):
        seq = list(base)
        state = seed * 2654435761 + 12345
        # Fisher-Yates with an LCG (Knuth MMIX constants, 32-bit).
        for i in range(len(seq) - 1, 0, -1):
            state = (state * 1103515245 + 12345) & 0xFFFFFFFF
            j = state % (i + 1)
            seq[i], seq[j] = seq[j], seq[i]
        # Keep runnable: a prepare must precede its commit.
        for idx, op in enumerate(seq):
            if op[0] == "commit":
                prep = ("prepare", op[1])
                prep_idx = seq.index(prep)
                if prep_idx > idx:
                    seq.remove(prep)
                    seq.insert(idx, prep)
        sequences.append(seq)
    return sequences


class CompactionTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lake-accept-")
        self.root = Path(self.tmp) / "root"
        self.root.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _apply_op(self, oracle, op):
        kind = op[0]
        if kind == "append":
            payload = {"X": APPEND_X, "Y": APPEND_Y, "Z": APPEND_Z}[op[1]]
            inp = write_input(self.tmp, "append-%s.json" % op[1], payload)
            result = run_cli(self.root, "append", str(inp))
            oracle.append(payload)
            self.assertEqual(result["status"], "appended")
            self.assertEqual(result["version"], oracle.head)
        elif kind == "delete":
            payload = (DELETE_SELECTED if op[1] == "selected"
                       else DELETE_OTHER)
            inp = write_input(self.tmp, "delete-%s.json" % op[1], payload)
            result = run_cli(self.root, "delete", str(inp))
            oracle.delete(payload)
            self.assertEqual(result["status"], "deleted")
            self.assertEqual(result["version"], oracle.head)
        elif kind == "prepare":
            result = run_cli(self.root, "prepare", op[1])
            plan = oracle.prepare(op[1])
            self.assertEqual(result["status"], "prepared")
            self.assertEqual(result["base"], plan["base"])
        elif kind == "commit":
            result = run_cli(self.root, "commit", op[1])
            expected = oracle.commit(op[1])
            self.assertEqual(result["status"], expected["status"],
                             "op=%s result=%s expected=%s"
                             % (op, result, expected))
            if expected["status"] == "committed":
                self.assertEqual(result["version"], expected["version"])
                self.assertEqual(result["version"], oracle.head)
            else:
                self.assertEqual(result["reason"], expected["reason"])
        else:  # pragma: no cover - guarded by construction
            raise AssertionError("unknown op %r" % (op,))

    def _assert_all_snapshots_match_oracle(self, oracle):
        state = run_cli(self.root, "state")
        # Every version the oracle ever published is still readable unless GC
        # intentionally pruned it; these runs never GC.
        self.assertEqual(sorted(state["retained"]),
                         sorted(oracle.versions))
        for version in range(0, oracle.head + 1):
            read = run_cli(self.root, "read", "--version", str(version))
            self.assertEqual(read["version"], version)
            self.assertEqual(
                [r["id"] for r in read["rows"]],
                oracle.visible_ids(version),
                "old-reader row set diverged at version %d" % version)
            self.assertEqual(sorted(read["deleted"]),
                             oracle.deleted_at(version))
            # Every object referenced by the manifest exists on disk.
            for obj in read["files"]:
                self.assertTrue(
                    (self.root / "objects" / (obj + ".json")).exists(),
                    "manifest v%d references missing object %s"
                    % (version, obj))

    def test_twenty_fixed_interleavings(self):
        sequences = fixed_interleavings(20)
        self.assertEqual(len(sequences), 20)
        covered_commits = covered_conflicts = covered_rebases = 0
        for idx, seq in enumerate(sequences):
            # Fresh lake per interleaving.
            root = Path(self.tmp) / ("case-%02d" % idx)
            root.mkdir()
            case_tmp = Path(self.tmp) / ("inputs-%02d" % idx)
            case_tmp.mkdir()
            old_root, old_input = self.root, self.tmp
            self.root, self.tmp = root, case_tmp
            try:
                oracle = bootstrap(root, case_tmp)
                for op in seq:
                    before = oracle.head
                    self._apply_op(oracle, op)
                    if op[0] == "commit":
                        outcome = oracle.plans[op[1]]["outcome"]
                        if outcome["status"] == "committed":
                            covered_commits += 1
                            if oracle.head > before:
                                covered_rebases += 1
                        else:
                            covered_conflicts += 1
                    self._assert_all_snapshots_match_oracle(oracle)
                # Repeating a finished plan is idempotent and never advances.
                for plan_name in ("p1", "p2"):
                    plan = oracle.plans[plan_name]
                    again = run_cli(root, "commit", plan_name)
                    self.assertEqual(again["status"],
                                     plan["outcome"]["status"])
                    if plan["outcome"]["status"] == "committed":
                        self.assertEqual(again["version"],
                                         plan["outcome"]["version"])
                    head_now = run_cli(root, "state")["head"]
                    self.assertEqual(head_now, oracle.head)
            finally:
                self.root, self.tmp = old_root, old_input
        # The fixed pool must exercise both happy-path rebases and aborts.
        self.assertGreater(covered_commits, 0)
        self.assertGreater(covered_conflicts, 0)
        self.assertGreater(covered_rebases, 0)

    # ------------------------------------------------------------ crashes
    def _retained_manifests(self):
        db = sqlite3.connect(self.root / "catalog.db")
        rows = db.execute(
            "SELECT version,files,deleted FROM snapshots ORDER BY version"
        ).fetchall()
        db.close()
        return [(v, json.loads(f), json.loads(d)) for v, f, d in rows]

    def _assert_manifest_integrity(self, oracle):
        manifests = self._retained_manifests()
        state = run_cli(self.root, "state")
        self.assertEqual([v for v, _, _ in manifests], state["retained"])
        for version, files, deleted in manifests:
            for obj in files:
                path = self.root / "objects" / (obj + ".json")
                self.assertTrue(path.exists(),
                                "retained manifest v%d lost object %s"
                                % (version, obj))
                # Object itself must be complete JSON, never a staged file.
                json.loads(path.read_text())
            read = run_cli(self.root, "read", "--version", str(version))
            self.assertEqual(sorted(read["deleted"]), sorted(deleted))
            if version in oracle.versions:
                # Oracle keeps every version forever; the lake may prune a
                # snapshot only when GC proves no retained reader uses it.
                self.assertEqual([r["id"] for r in read["rows"]],
                                 oracle.visible_ids(version))
        # Head truth: row ids and delete state.
        head = run_cli(self.root, "read")
        self.assertEqual([r["id"] for r in head["rows"]],
                         oracle.visible_ids(oracle.head))
        self.assertEqual(sorted(head["deleted"]),
                         oracle.deleted_at(oracle.head))
        # No half-written staged objects are ever visible.
        self.assertEqual(list((self.root / "objects").glob("*.tmp")), [])

    def test_crash_after_data_then_recover(self):
        oracle = bootstrap(self.root, self.tmp)
        run_cli(self.root, "prepare", "job")
        plan = oracle.prepare("job")
        # An append lands after prepare: recovery must rebase over it.
        inp = write_input(self.tmp, "ax.json", APPEND_X)
        run_cli(self.root, "append", str(inp))
        oracle.append(APPEND_X)

        run_cli(self.root, "commit", "job", "--crash", "after-data",
                expect_exit=CRASH_EXIT)
        # Nothing metadata-visible happened.
        state = run_cli(self.root, "state")
        self.assertEqual(state["head"], oracle.head)
        plan_row = [p for p in state["plans"] if p["name"] == "job"][0]
        self.assertIsNone(plan_row["outcome"])
        # Plain retry completes the commit exactly once.
        result = run_cli(self.root, "commit", "job")
        expected = oracle.commit("job")
        self.assertEqual(result["status"], "committed")
        self.assertEqual(result["version"], expected["version"])
        self._assert_manifest_integrity(oracle)
        # Retry again: same stored outcome, no second snapshot.
        again = run_cli(self.root, "commit", "job")
        self.assertEqual(again, result)
        self.assertEqual(run_cli(self.root, "state")["head"], oracle.head)

    def test_crash_after_publish_then_recover(self):
        oracle = bootstrap(self.root, self.tmp)
        run_cli(self.root, "prepare", "job")
        oracle.prepare("job")
        # The commit is guaranteed to publish before the injected crash, so
        # the oracle records it before the failing call.
        expected = oracle.commit("job")
        run_cli(self.root, "commit", "job", "--crash", "after-publish",
                expect_exit=CRASH_EXIT)
        # The publish is durable even though the caller saw exit 97.
        state = run_cli(self.root, "state")
        self.assertEqual(state["head"], expected["version"])
        self._assert_manifest_integrity(oracle)
        # Retry returns the stored outcome instead of publishing again.
        again = run_cli(self.root, "commit", "job")
        self.assertEqual(again["status"], "committed")
        self.assertEqual(again["version"], expected["version"])
        self.assertEqual(run_cli(self.root, "state")["head"], oracle.head)

    def test_gc_crash_before_unlink_and_collection(self):
        oracle = bootstrap(self.root, self.tmp)
        # Pin an early snapshot so an old reader must keep working post-GC.
        run_cli(self.root, "pin", "1")
        # Two compactions produce obsolete objects; the second also proves
        # that closed plans do not retain their baselines forever.
        run_cli(self.root, "prepare", "job1")
        oracle.prepare("job1")
        run_cli(self.root, "commit", "job1")
        oracle.commit("job1")
        # Need two files again for a second plan.
        inp = write_input(self.tmp, "ay.json", APPEND_Y)
        run_cli(self.root, "append", str(inp))
        oracle.append(APPEND_Y)
        run_cli(self.root, "prepare", "job2")
        oracle.prepare("job2")
        # Crash point 3: before any unlink. Nothing may change on disk.
        before = run_cli(self.root, "state")
        run_cli(self.root, "gc", "--crash", "before-unlink",
                expect_exit=CRASH_EXIT)
        after_crash = run_cli(self.root, "state")
        self.assertEqual(after_crash, before)
        for obj in before["objects"]:
            self.assertTrue(
                (self.root / "objects" / (obj + ".json")).exists())
        # An in-flight plan baseline still protects its snapshot here.
        self.assertIn(oracle.plans["job2"]["base"], after_crash["retained"])

        # Complete the plan, then run GC for real.
        run_cli(self.root, "commit", "job2")
        oracle.commit("job2")
        gc = run_cli(self.root, "gc")
        self.assertEqual(gc["status"], "collected")
        state = run_cli(self.root, "state")
        # Head and the pinned snapshot survive; everything else is pruned.
        self.assertEqual(state["head"], oracle.head)
        self.assertIn(1, state["retained"])
        self.assertIn(oracle.head, state["retained"])
        self.assertEqual(set(state["pins"]), {1})
        # Every retained manifest still resolves, including the pinned one.
        pinned = run_cli(self.root, "read", "--version", "1")
        self.assertEqual([r["id"] for r in pinned["rows"]],
                         oracle.visible_ids(1))
        self._assert_manifest_integrity(oracle)
        # Removed objects are exactly the unreferenced set diagnosed earlier
        # minus objects that later snapshots still use.
        on_disk = set(state["objects"])
        for obj in gc["removed"]:
            self.assertNotIn(obj, on_disk)

    def test_gc_keeps_inflight_baseline_and_unpins(self):
        oracle = bootstrap(self.root, self.tmp)
        run_cli(self.root, "prepare", "job")
        plan = oracle.prepare("job")
        run_cli(self.root, "pin", "2")
        run_cli(self.root, "unpin", "2")
        gc = run_cli(self.root, "gc")
        state = run_cli(self.root, "state")
        # In-flight plan baseline is retained even though it is not pinned.
        self.assertIn(plan["base"], state["retained"])
        self.assertNotIn(2, state["pins"])
        self._assert_manifest_integrity(oracle)
        # Closing the plan allows its baseline to be collected.
        run_cli(self.root, "commit", "job")
        oracle.commit("job")
        run_cli(self.root, "gc")
        state = run_cli(self.root, "state")
        self.assertEqual(state["retained"], [oracle.head])
        self._assert_manifest_integrity(oracle)

    def test_conflict_paths_and_delete_visibility(self):
        oracle = bootstrap(self.root, self.tmp)
        # Two plans over identical inputs; first commit wins, second conflicts.
        run_cli(self.root, "prepare", "a")
        oracle.prepare("a")
        run_cli(self.root, "prepare", "b")
        oracle.prepare("b")
        first = run_cli(self.root, "commit", "a")
        oracle.commit("a")
        self.assertEqual(first["status"], "committed")
        second = run_cli(self.root, "commit", "b")
        self.assertEqual(second["status"], "conflict")
        self.assertEqual(second["reason"], "selected-file-replaced")
        self.assertEqual(second["version"], oracle.head)
        # Version must not advance on conflict.
        self.assertEqual(run_cli(self.root, "state")["head"], oracle.head)

        # Delete marker survives a rebase: delete r2, then compact the new
        # first two files and confirm r2 stays invisible.
        inp = write_input(self.tmp, "d.json", ["r2"])
        run_cli(self.root, "delete", str(inp))
        oracle.delete(["r2"])
        run_cli(self.root, "prepare", "c")
        oracle.prepare("c")
        committed = run_cli(self.root, "commit", "c")
        oracle.commit("c")
        self.assertEqual(committed["status"], "committed")
        head = run_cli(self.root, "read")
        self.assertNotIn("r2", [r["id"] for r in head["rows"]])
        self.assertIn("r2", head["deleted"])
        self.assertEqual([r["id"] for r in head["rows"]],
                         oracle.visible_ids(oracle.head))

        # A delete landing between prepare and commit on selected rows aborts.
        # Append first so two files are available again after compaction c.
        inp = write_input(self.tmp, "aw.json", APPEND_X)
        run_cli(self.root, "append", str(inp))
        oracle.append(APPEND_X)
        run_cli(self.root, "prepare", "d")
        oracle.prepare("d")
        inp = write_input(self.tmp, "d2.json", ["r3"])
        run_cli(self.root, "delete", str(inp))
        oracle.delete(["r3"])
        clash = run_cli(self.root, "commit", "d")
        expected = oracle.commit("d")
        self.assertEqual(clash["status"], "conflict")
        self.assertEqual(clash["reason"], "conflicting-delete")
        self.assertEqual(expected["status"], "conflict")
        self.assertEqual(run_cli(self.root, "state")["head"], oracle.head)
        self._assert_manifest_integrity(oracle)

        # Delete on an appended (non-selected) file is safe to rebase.
        inp = write_input(self.tmp, "az.json", APPEND_Z)
        run_cli(self.root, "append", str(inp))
        oracle.append(APPEND_Z)
        run_cli(self.root, "prepare", "e")
        oracle.prepare("e")
        inp = write_input(self.tmp, "dz.json", ["z1"])
        run_cli(self.root, "delete", str(inp))
        oracle.delete(["z1"])
        ok = run_cli(self.root, "commit", "e")
        oracle.commit("e")
        self.assertEqual(ok["status"], "committed")
        # z1 stays invisible after the rebase compaction.
        head = run_cli(self.root, "read")
        self.assertNotIn("z1", [r["id"] for r in head["rows"]])
        self.assertIn("z1", head["deleted"])
        self._assert_manifest_integrity(oracle)


if __name__ == "__main__":
    unittest.main(verbosity=2)
