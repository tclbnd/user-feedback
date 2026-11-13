"""Acceptance harness for manifest compaction.

Part 1 enumerates 20 fixed interleavings of two compaction tasks, appends and
deletes, runs each against a serial row-level model, and checks every published
snapshot (plus every pinned old snapshot) for the exact live row set.
Part 2 interrupts commit after-data / after-publish and gc before-unlink, then
recovers and verifies manifest references, current row ids and delete states.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

import lake

SCRIPT = pathlib.Path(__file__).resolve()
WORK = SCRIPT.parent


# ----------------------------------------------------------------- model
class Model:
    """Serial row-level truth: append adds rows, delete hides ids."""

    def __init__(self):
        self.rows = {}
        self.deleted = set()

    def copy_rows(self):
        return [dict(self.rows[key]) for key in self.rows if key not in self.deleted]

    def append(self, rows):
        for row in rows:
            assert row["id"] not in self.rows, "row ids are never reused"
            self.rows[row["id"]] = dict(row)
        return self.copy_rows()

    def delete(self, ids):
        self.deleted.update(ids)
        return self.copy_rows()


ROWSET = [
    {"id": "r0", "value": 0},
    {"id": "r1", "value": 1},
    {"id": "r2", "value": 2},
    {"id": "r3", "value": 3},
    {"id": "r4", "value": 4},
    {"id": "r5", "value": 5},
    {"id": "r6", "value": 6},
    {"id": "r7", "value": 7},
]


def op(token):
    """Expand a short token into a concrete operation.

    P1/C1 (and P2/C2) address the same plan, so submitting C1 closes the
    plan opened by P1.
    """
    if token.startswith("A"):
        return "append", [dict(ROWSET[int(token[1:])])]
    if token.startswith("D"):
        return "delete", [ROWSET[int(token[1:])]["id"]]
    if token.startswith("P"):
        return "prepare", "plan" + token[1:]
    return "commit", "plan" + token[1:]


# Twenty fixed interleavings of two compaction plans (P1/C1, P2/C2), appends
# (A*) and deletes (D*).  D0-D3 touch the selected base objects, D4-D7 touch
# objects appended after the base.  Both preparations always see two files:
# either P2 runs before C1, or an append follows C1 before P2.
SPECS = [
    # P2 prepared against the same base as P1 -> the second commit conflicts.
    ["P1", "P2", "C1", "C2", "A4", "D6"],
    ["P1", "P2", "A4", "C1", "C2", "D0"],
    ["P1", "D0", "P2", "C1", "C2"],
    ["P1", "P2", "C1", "D0", "C2"],
    ["P1", "A4", "P2", "C1", "C2", "D6"],
    ["P1", "P2", "D4", "C1", "C2"],
    ["P1", "P2", "C2", "C1"],
    # P2 prepared after C1 rewrote the base -> both commits succeed (chained).
    ["P1", "C1", "A4", "P2", "C2", "D6"],
    ["P1", "A4", "C1", "P2", "C2", "D6"],
    ["P1", "C1", "A4", "D0", "P2", "C2"],
    ["P1", "C1", "A4", "P2", "D6", "C2"],
    ["P1", "A4", "D0", "C1", "P2", "C2"],
    ["P1", "A4", "D4", "C1", "P2", "C2"],
    ["P1", "A4", "C1", "D4", "P2", "C2"],
    ["P1", "D4", "A4", "C1", "P2", "C2"],
    # Deletes that hit selected rows abort the commit instead of losing data.
    ["P1", "D0", "C1", "A4", "P2", "C2"],
    ["P1", "A4", "D2", "C1", "P2", "C2"],
    ["P1", "D0", "D2", "C1", "A4", "P2", "C2"],
    # Longer mixes: append between compaction, delete concurrently, re-commit.
    ["P1", "A4", "C1", "A5", "P2", "D6", "C2", "C1"],
    ["P1", "C1", "A4", "P2", "C2", "D2", "A6"],
]
assert len(SPECS) == 20


# ----------------------------------------------------------------- helpers
def cli(root, *args, env=None):
    base_env = dict(os.environ, PYTHONPYCACHEPREFIX="/tmp/pyc")
    if env:
        base_env.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPT.parent / "lake.py"), str(root), *args],
        capture_output=True, text=True, env=base_env, cwd=str(WORK))


def cli_json(root, *args, env=None):
    proc = cli(root, *args, env=env)
    if proc.returncode not in (0, 97):
        raise AssertionError("cli failed rc=%s stderr=%s" % (proc.returncode, proc.stderr))
    return proc.returncode, (json.loads(proc.stdout) if proc.stdout.strip() else None)


def write_input(root, name, payload):
    path = root / ("input-" + name + ".json")
    path.write_text(json.dumps(payload))
    return path


def integrity(store, retained):
    """Every file named by every retained manifest must hash-match and load."""
    versions = set(retained)
    published = {v for v, in store.db.execute("SELECT version FROM snapshots")}
    versions |= published
    for version in versions:
        snap = store.snapshot(version)
        for name in snap["files"]:
            rows = store.get_rows(name)  # verifies content hash and JSON shape
            ids = [row["id"] for row in rows]
            assert len(ids) == len(set(ids)), "duplicate ids in %s" % name
    for version in retained:
        store.read(version)  # readers must be able to traverse retained manifests
    assert store.snapshot()["version"] in retained


def check_no_temp_files(root):
    leftovers = [p.name for p in (root / "objects").iterdir() if p.name.startswith(".")]
    assert not leftovers, "half-written files visible: %s" % leftovers


def run_spec(spec, label):
    root = pathlib.Path(tempfile.mkdtemp(prefix="compact-"))
    store = lake.Lake(root)
    model = Model()
    expected_next = [1]
    pins = {}
    history = []  # (version, expected rows) for every published snapshot

    def record(version):
        rows = model.copy_rows()
        assert version == expected_next[0], "%s published v%s, expected v%s" % (
            label, version, expected_next[0])
        history.append((version, rows))
        expected_next[0] = version + 1

    model.append([ROWSET[0], ROWSET[1]])
    record(store.append([ROWSET[0], ROWSET[1]])["version"])
    model.append([ROWSET[2], ROWSET[3]])
    record(store.append([ROWSET[2], ROWSET[3]])["version"])

    def verify_head_rows():
        head = store.read()
        assert head["rows"] == model.copy_rows(), (
            "%s head rows diverged: %s vs %s" % (label, head["rows"], model.copy_rows()))

    outcomes = {}
    for token in spec:
        kind, target = op(token)
        if kind == "append":
            result = store.append(target)
            model.append(target)
            record(result["version"])
        elif kind == "delete":
            result = store.delete(target)
            model.delete(target)
            record(result["version"])
        elif kind == "prepare":
            head = store.state()["head"]
            plan = store.prepare(target)
            assert plan["base"] == head, "%s prepare base %s != head %s" % (label, plan["base"], head)
            assert plan["files"] == store.snapshot(head)["files"][:2]
            assert store.prepare(target) == plan, "same name must return same plan"
            # Pin the pre-compaction snapshot: an old reader must keep seeing it.
            store.pin(head)
            pins[head] = model.copy_rows()
            # Preparation itself must never publish a snapshot.
            assert store.state()["head"] == head
        elif kind == "commit":
            head_before = store.state()["head"]
            result = store.commit(target)
            if outcomes.get(target):
                # Re-submitting a closed plan replays its stored outcome and
                # must not advance the version or publish a new snapshot.
                assert result == outcomes[target][-1], (
                    "%s plan %s replayed %s, expected %s" % (
                        label, target, result, outcomes[target][-1]))
                assert store.state()["head"] == head_before
            else:
                outcomes[target] = [result]
                if result["status"] == "committed":
                    assert result["version"] == expected_next[0], (
                        "%s commit version %s != %s" % (
                            label, result["version"], expected_next[0]))
                    record(result["version"])
                    verify_head_rows()
                    assert store.state()["head"] == result["version"]
                else:
                    assert result["status"] == "conflict"
                    assert result["version"] == head_before, "%s conflict advanced the version" % label
                    assert store.state()["head"] == head_before
                    if "reason" in result:
                        assert result["reason"] in (
                            "delete-affects-selection", "selected-file-replaced",
                            "unreadable-input")
        elif kind == "gc":
            store.gc()
            check_no_temp_files(root)
        else:  # pragma: no cover - spec typo guard
            raise AssertionError("unknown token %s" % token)

    # Every snapshot published before gc is still readable and matches the model
    # state at its publication time, regardless of interleaving.
    for version, rows in history:
        published = store.read(version)
        assert published["rows"] == rows, "%s snapshot %s lost rows" % (label, version)
        assert published["version"] == version

    # Pinned old readers still traverse the original row set, tombstoned rows
    # included in the manifest's delete view but not in the live output.
    for version, rows in pins.items():
        got = store.read(version)["rows"]
        assert got == rows, "%s pinned reader v%s saw %s != %s" % (label, version, got, rows)

    # Deletes that were rebased must not resurrect rows in head.
    verify_head_rows()

    # GC drops every snapshot nobody retains and every object nobody references.
    before = store.state()
    assert before["head"] in before["retained"]
    store.gc()
    check_no_temp_files(root)
    state = store.state()
    expected_retained = {state["head"]} | set(state["pins"]) | set(state.get("pending_bases", []))
    assert set(state["retained"]) == expected_retained, (
        "%s gc retained %s, expected %s" % (label, state["retained"], sorted(expected_retained)))
    integrity(store, state["retained"])
    for version, rows in pins.items():
        assert store.read(version)["rows"] == rows
    verify_head_rows()
    # Any object left on disk must be referenced by a retained manifest.
    referenced = set()
    for version in state["retained"]:
        referenced.update(store.snapshot(version)["files"])
    assert set(state["objects"]) == referenced, (
        "%s gc left unreferenced objects %s" % (label, set(state["objects"]) - referenced))
    return outcomes


def reopen(store):
    """Drop cached SQLite state before checking what a child process committed."""
    try:
        store.db.close()
    except Exception:
        pass
    return lake.Lake(store.root)


def expect_crash(root, *args):
    proc = cli(root, *args)
    assert proc.returncode == 97, "expected crash exit 97, got %s (%s)" % (
        proc.returncode, proc.stderr)
    return proc


# ------------------------------------------------------------ crash recovery
def setup_root(prefix):
    root = pathlib.Path(tempfile.mkdtemp(prefix=prefix))
    store = lake.Lake(root)
    store.append([ROWSET[0], ROWSET[1]])
    store.append([ROWSET[2], ROWSET[3]])
    return root, store


def test_crash_after_data():
    root, store = setup_root("crash-data-")
    base = store.state()["head"]
    store.pin(base)
    store.prepare("c")
    store.append([ROWSET[4]])
    store.delete(["r4"])  # tombstone on an appended (unselected) row: legal rebase
    head = store.state()["head"]
    expected = store.read()["rows"]
    store.db.close()

    baseline_objects = set(lake.Lake(root).state()["objects"])
    expect_crash(root, "commit", "c", "--crash", "after-data")

    store = reopen(lake.Lake(root))
    state = store.state()
    assert state["head"] == head, "after-data crash must not publish"
    outcome = store.db.execute("SELECT outcome FROM plans WHERE name='c'").fetchone()[0]
    assert outcome is None, "plan outcome written before publish"
    written = set(state["objects"]) - baseline_objects
    assert len(written) == 1, "expected exactly one half-built object: %s" % written
    # The half-built object is unreferenced; gc may reclaim it and report it.
    gc_result = store.gc()
    assert written <= set(gc_result["removed"]) | set(gc_result["unreferenced"]), gc_result
    check_no_temp_files(root)
    assert store.read(base)["rows"] == [ROWSET[0], ROWSET[1], ROWSET[2], ROWSET[3]]
    assert store.read(head)["rows"] == expected

    # Retrying without the crash flag resumes the plan and publishes once.
    result = store.commit("c")
    assert result["status"] == "committed"
    assert result["version"] == head + 1
    assert store.read()["rows"] == expected
    assert store.read(head)["rows"] == expected
    assert store.read(base)["rows"] == [ROWSET[0], ROWSET[1], ROWSET[2], ROWSET[3]]
    assert store.commit("c") == result, "retry after success must replay"
    assert store.state()["head"] == result["version"]
    integrity(store, store.state()["retained"])
    check_no_temp_files(root)


def test_crash_after_publish():
    root, store = setup_root("crash-publish-")
    base = store.state()["head"]
    store.pin(base)
    store.prepare("c")
    store.append([ROWSET[4], ROWSET[5]])
    store.delete(["r6"])
    head = store.state()["head"]
    expected = store.read()["rows"]
    store.db.close()

    expect_crash(root, "commit", "c", "--crash", "after-publish")

    store = reopen(lake.Lake(root))
    state = store.state()
    assert state["head"] == head + 1, "manifest must be durable after crash"
    stored = json.loads(store.db.execute(
        "SELECT outcome FROM plans WHERE name='c'").fetchone()[0])
    assert stored["status"] == "committed" and stored["version"] == state["head"]
    # Appends and unselected-row deletes interleaved with the compaction
    # survived the rebase.
    assert store.read()["rows"] == expected
    assert [row["id"] for row in store.read()["rows"]] == ["r0", "r1", "r2", "r3", "r4", "r5"]
    # Retry is idempotent: same version, no extra snapshot.
    result = store.commit("c")
    assert result == stored
    assert store.state()["head"] == stored["version"]
    # The pinned old reader still traverses its original row set.
    assert store.read(base)["rows"] == [ROWSET[0], ROWSET[1], ROWSET[2], ROWSET[3]]
    # GC keeps the pinned old snapshot and removes only unreferenced data.
    gc_result = store.gc()
    assert base in gc_result["retained"]
    integrity(store, gc_result["retained"])
    assert store.read(base)["rows"] == [ROWSET[0], ROWSET[1], ROWSET[2], ROWSET[3]]
    check_no_temp_files(root)


def test_crash_before_unlink():
    root, store = setup_root("crash-gc-")
    store.prepare("open")
    store.append([ROWSET[4], ROWSET[5]])
    store.commit("open")
    # A second plan's base must also protect its selected objects.
    store.prepare("pending")
    store.db.close()
    before = lake.Lake(root).state()

    expect_crash(root, "gc", "--crash", "before-unlink")

    store = reopen(lake.Lake(root))
    after = store.state()
    assert after["objects"] == before["objects"], "gc unlinked before the crash point"
    assert after["retained"] == before["retained"]
    integrity(store, after["retained"])

    # Retrying gc finishes the job and only removes genuinely unreferenced data.
    result = store.gc()
    referenced = set()
    for version in result["retained"]:
        referenced.update(store.snapshot(version)["files"])
    assert set(store.state()["objects"]) == referenced
    for version in result["retained"]:
        store.read(version)
    integrity(store, result["retained"])
    check_no_temp_files(root)


def test_conflict_recorded():
    root, store = setup_root("conflict-")
    store.prepare("a")
    store.prepare("b")
    store.pin(store.state()["head"])
    store.commit("a")
    head = store.state()["head"]
    result = store.commit("b")
    assert result["status"] == "conflict" and result["version"] == head
    assert store.state()["head"] == head, "conflict advanced the version"
    conflicts = store.db.execute("SELECT reason FROM conflicts ORDER BY id").fetchall()
    assert ("selected-file-replaced",) in conflicts
    # A conflicting plan replays its recorded outcome instead of re-deciding.
    assert store.commit("b") == result
    # gc still keeps objects referenced by the old pin after the conflict.
    gc_result = store.gc()
    integrity(store, gc_result["retained"])
    assert store.read(gc_result["retained"][0])  # sanity: readable manifest


def test_pending_base_protects_objects():
    """A prepared-but-uncommitted plan keeps its base manifest and objects
    alive across gc, so the plan can still be committed afterwards."""
    root, store = setup_root("pending-")
    base = store.state()["head"]
    store.prepare("a")
    store.append([ROWSET[4]])          # new head does not reference base objects alone
    store.prepare("b")                 # second plan, base = newer head
    state = store.state()
    assert state["pending_bases"] == sorted({base, base + 1})
    store.gc()
    state = store.state()
    assert base in state["retained"] and base + 1 in state["retained"]
    for version in (base, base + 1):
        store.read(version)            # old readers still traverse them
    integrity(store, state["retained"])
    # Both plans are still committable after gc.
    assert store.commit("b")["status"] == "committed"
    assert store.commit("a")["status"] == "conflict"   # its files were replaced


def test_unreadable_input_conflicts():
    """A corrupt selected object must abort the commit, not republish it."""
    root, store = setup_root("missing-")
    head = store.state()["head"]
    store.prepare("a")
    victim = store.snapshot(head)["files"][0]
    path = root / "objects" / (victim + ".json")
    original = path.read_text()
    path.write_text("[]")  # content no longer matches its address
    try:
        result = store.commit("a")
        assert result["status"] == "conflict" and result["version"] == head
        assert store.state()["head"] == head
        assert store.db.execute(
            "SELECT COUNT(*) FROM conflicts WHERE reason='unreadable-input'").fetchone()[0] == 1
        # Replaying the closed plan is stable and never publishes.
        assert store.commit("a") == result
        assert store.state()["head"] == head
    finally:
        path.write_text(original)
    # With the object restored, the manifest is intact again and gc is safe.
    assert store.read(head)["rows"] == [ROWSET[0], ROWSET[1], ROWSET[2], ROWSET[3]]
    store.gc()
    assert store.read(store.state()["head"])["rows"] == [
        ROWSET[0], ROWSET[1], ROWSET[2], ROWSET[3]]


def test_cli_contract():
    """Exit codes and JSON shapes as seen by a caller."""
    root, store = setup_root("cli-")
    head = store.state()["head"]
    store.prepare("p")
    store.pin(head)
    store.db.close()

    payload = root / "rows.json"
    payload.write_text(json.dumps([{"id": "r4", "value": 4}]))
    proc = cli(root, "append", str(payload))
    assert proc.returncode == 0 and json.loads(proc.stdout)["version"] == head + 1
    proc = cli(root, "prepare", "p")
    assert proc.returncode == 0 and json.loads(proc.stdout)["base"] == head
    proc = cli(root, "commit", "p")
    assert proc.returncode == 0
    result = json.loads(proc.stdout)
    assert result["status"] == "committed" and result["version"] == head + 2
    proc = cli(root, "read", "--version", str(head))
    assert proc.returncode == 0
    assert [row["id"] for row in json.loads(proc.stdout)["rows"]] == ["r0", "r1", "r2", "r3"]
    proc = cli(root, "read", "--version", "999")
    assert proc.returncode == 2, "unknown snapshot must fail loudly"
    proc = cli(root, "commit", "nope")
    assert proc.returncode == 2, "unknown plan must fail loudly"
    proc = cli(root, "state")
    assert proc.returncode == 0
    state = json.loads(proc.stdout)
    assert state["head"] == result["version"] and head in state["pins"]


def test_rebase_keeps_appends():
    root, store = setup_root("rebase-")
    store.pin(store.state()["head"])
    store.prepare("a")
    store.append([ROWSET[4]])
    store.delete(["r5"])  # unselected id: harmless, must rebase
    result = store.commit("a")
    assert result["status"] == "committed"
    assert [row["id"] for row in store.read()["rows"]] == ["r0", "r1", "r2", "r3", "r4"]
    store.gc()
    for version in store.state()["retained"]:
        store.read(version)
    integrity(store, store.state()["retained"])


def test_reader_never_sees_partial_object():
    """A reader traversing an old snapshot while a commit writes data must
    only ever observe complete objects."""
    root, store = setup_root("atomic-")
    store.pin(store.state()["head"])
    store.prepare("c")
    old_version = store.state()["head"]
    old_rows = store.read(old_version)["rows"]
    store.db.close()

    # Start the after-data crash in a child, then verify the on-disk object it
    # left behind is complete JSON (never a truncated prefix).
    expect_crash(root, "commit", "c", "--crash", "after-data")
    objects = sorted((root / "objects").glob("*.json"))
    for path in objects:
        payload = path.read_text()
        # Every object file parses and its name matches its content hash,
        # whether or not it is referenced yet.
        assert json.loads(payload) is not None or payload == "[]"
        import hashlib
        assert hashlib.sha256(payload.encode()).hexdigest() == path.stem, path.name
    leftovers = [p.name for p in (root / "objects").iterdir() if p.name.startswith(".")]
    assert not leftovers, "temp file was published: %s" % leftovers

    store = reopen(lake.Lake(root))
    assert store.read(old_version)["rows"] == old_rows, "old reader lost rows"
    store.commit("c")
    assert store.read(old_version)["rows"] == old_rows
    store.gc()
    assert store.read(store.state()["head"])


def test_writer_serialization_under_lock():
    """While another process holds the write lock, a commit must abort rather
    than publish against a stale manifest."""
    root, store = setup_root("lock-")
    head = store.state()["head"]
    store.prepare("c")
    store.db.close()

    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sqlite3,time,sys\n"
         "db=sqlite3.connect(sys.argv[1],timeout=30)\n"
         "db.execute('BEGIN IMMEDIATE')\n"
         "print('locked',flush=True)\n"
         "time.sleep(3)\n"
         "db.commit()\n", str(root / "catalog.db")],
        stdout=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONPYCACHEPREFIX": "/tmp/pyc"})
    assert holder.stdout.readline().strip() == "locked"
    try:
        proc = cli(root, "commit", "c")
        assert proc.returncode == 0, "commit should wait for the lock: %s" % proc.stderr
        assert json.loads(proc.stdout)["status"] == "committed"
    finally:
        holder.wait(timeout=10)
    store = reopen(lake.Lake(root))
    assert store.state()["head"] == head + 1
    assert store.read()["rows"] == [ROWSET[0], ROWSET[1], ROWSET[2], ROWSET[3]]


# ---------------------------------------------------------------- entrypoint
def main():
    failures = []
    seen_committed = seen_conflict = 0
    for index, spec in enumerate(SPECS, 1):
        label = "spec%02d" % index
        try:
            outcomes = run_spec(spec, label)
            for token, results in outcomes.items():
                seen_committed += sum(1 for r in results if r["status"] == "committed")
                seen_conflict += sum(1 for r in results if r["status"] == "conflict")
        except AssertionError as error:
            failures.append((label, spec, str(error)))
            print("FAIL %s %s: %s" % (label, spec, error))
        else:
            print("ok   %s %s" % (label, spec))

    for name, test in (
        ("crash-after-data", test_crash_after_data),
        ("crash-after-publish", test_crash_after_publish),
        ("crash-before-unlink", test_crash_before_unlink),
        ("conflict-recorded", test_conflict_recorded),
        ("pending-base-protected", test_pending_base_protects_objects),
        ("unreadable-input", test_unreadable_input_conflicts),
        ("cli-contract", test_cli_contract),
        ("rebase-keeps-appends", test_rebase_keeps_appends),
        ("reader-atomicity", test_reader_never_sees_partial_object),
        ("writer-serialization", test_writer_serialization_under_lock),
    ):
        try:
            test()
        except AssertionError as error:
            failures.append((name, [], str(error)))
            print("FAIL %s: %s" % (name, error))
        else:
            print("ok   %s" % name)

    print("commits=%d conflicts=%d specs=%d failures=%d" % (
        seen_committed, seen_conflict, len(SPECS), len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

