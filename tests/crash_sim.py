#!/usr/bin/env python3
"""Crash injection and page-by-page recovery verification.

Simulates power loss by truncating the WAL or the page file at chosen write
points, then runs `store.py recover` (optionally several times, crashing in
the middle of undo) and verifies that:

  * every surviving page has a valid CRC (no torn page is ever trusted);
  * every value written by a committed transaction is present;
  * every value written by a transaction without COMMIT is gone;
  * each page LSN equals the LSN of the last record that affected it.

The expected state is derived independently from the *surviving* WAL by
replaying its UPDATE / CLR records onto empty pages - the recovery code under
test is never used to compute the oracle.

Run:  python3 tests/crash_sim.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pager  # noqa: E402
from store import Engine  # noqa: E402
from wal import Log  # noqa: E402

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------- #
# Workload builders
# --------------------------------------------------------------------- #
def workload_with_checkpoint():
    """Committed tx1, checkpoint, committed tx3, active tx2, torn flush.

    Pages are flushed cleanly; the scenario cuts the page file itself to
    produce a CRC-detectable torn page.
    """
    return [
        {"op": "begin", "tx": 1},
        {"op": "set", "tx": 1, "page": 0, "slot": 0, "value": "alice=100"},
        {"op": "set", "tx": 1, "page": 1, "slot": 3, "value": "bob=40"},
        {"op": "commit", "tx": 1},
        {"op": "begin", "tx": 2},
        {"op": "set", "tx": 2, "page": 1, "slot": 0, "value": "carol=7"},
        {"op": "checkpoint"},
        {"op": "begin", "tx": 3},
        {"op": "set", "tx": 3, "page": 0, "slot": 3, "value": "dave=55"},
        {"op": "commit", "tx": 3},
        {"op": "set", "tx": 2, "page": 1, "slot": 0, "value": "carol=9"},
        {"op": "flush", "page": 1},
        {"op": "flush", "page": 0},
        {"op": "crash"},
    ]


def tear_page(directory, pid, keep=pager.PAGE_SIZE // 2):
    """Power loss halfway through writing page `pid`: keep only `keep` bytes."""
    truncate_file(os.path.join(directory, "pages.db"),
                  pid * pager.PAGE_SIZE + keep)


def workload_no_checkpoint():
    """No checkpoint at all; redo must start from LSN 1."""
    return [
        {"op": "begin", "tx": 1},
        {"op": "set", "tx": 1, "page": 0, "slot": 0, "value": "keep-1"},
        {"op": "commit", "tx": 1},
        {"op": "begin", "tx": 2},
        {"op": "set", "tx": 2, "page": 0, "slot": 1, "value": "drop-2"},
        {"op": "set", "tx": 2, "page": 2, "slot": 2, "value": "drop-2b"},
        {"op": "flush", "page": 0},
        {"op": "crash"},
    ]


def workload_repeated_crashes():
    """One loser transaction with four updates, enough for repeated crashes."""
    return [
        {"op": "begin", "tx": 1},
        {"op": "set", "tx": 1, "page": 0, "slot": 0, "value": "committed"},
        {"op": "commit", "tx": 1},
        {"op": "begin", "tx": 7},
        {"op": "set", "tx": 7, "page": 0, "slot": 1, "value": "u1"},
        {"op": "set", "tx": 7, "page": 1, "slot": 0, "value": "u2"},
        {"op": "set", "tx": 7, "page": 1, "slot": 1, "value": "u3"},
        {"op": "set", "tx": 7, "page": 2, "slot": 0, "value": "u4"},
        {"op": "flush", "page": 0},
        {"op": "crash"},
    ]


def workload_two_checkpoints():
    """Two checkpoints; analysis must start from the last complete one."""
    return [
        {"op": "begin", "tx": 1},
        {"op": "set", "tx": 1, "page": 0, "slot": 0, "value": "v1"},
        {"op": "checkpoint"},
        {"op": "commit", "tx": 1},
        {"op": "begin", "tx": 2},
        {"op": "set", "tx": 2, "page": 1, "slot": 0, "value": "v2"},
        {"op": "checkpoint"},
        {"op": "set", "tx": 2, "page": 1, "slot": 1, "value": "v3"},
        # Clean flush first; the scenario cuts the page file afterwards to
        # produce a real CRC-detectable torn page (the store's synthetic torn
        # flush keeps the new header/CRC and is indistinguishable from a good
        # page).
        {"op": "flush", "page": 1},
        {"op": "crash"},
    ]


# --------------------------------------------------------------------- #
# Crash injection helpers
# --------------------------------------------------------------------- #
def truncate_file(path, nbytes):
    with open(path, "r+b") as fh:
        fh.truncate(nbytes)


def wal_offset_of_lsn(directory, lsn):
    """Byte offset in wal.log right after the line carrying `lsn`."""
    path = os.path.join(directory, "wal.log")
    with open(path, "rb") as fh:
        data = fh.read()
    off = 0
    for line in data.split(b"\n"):
        off += len(line) + 1
        if line.strip() and json.loads(line.decode())["lsn"] == lsn:
            return off
    raise AssertionError("lsn %d not found" % lsn)


def find_lsn(directory, predicate):
    for r in Log(os.path.join(directory, "wal.log")).records():
        if predicate(r):
            return r["lsn"]
    raise AssertionError("record not found")


def run_recover(directory, crash_after_undo=None):
    cmd = [sys.executable, os.path.join(WORK, "store.py"), "recover", directory]
    if crash_after_undo is not None:
        cmd += ["--crash-after-undo", str(crash_after_undo)]
    return subprocess.run(cmd, capture_output=True, text=True)


# --------------------------------------------------------------------- #
# Independent oracle
# --------------------------------------------------------------------- #
def expected_state(directory):
    """Replay surviving UPDATE/CLR records onto empty pages.

    Returns {pid: (last_lsn, slots)}. Pages only named by FPIs but never
    updated are omitted.
    """
    pages, last_lsn = {}, {}
    recs = Log(os.path.join(directory, "wal.log")).records()
    committed = {r["tx"] for r in recs if r["type"] == "COMMIT"}
    for r in recs:
        if r["type"] == "UPDATE" and r["tx"] not in committed:
            continue
        if r["type"] not in ("UPDATE", "CLR"):
            continue
        pid = r["page"]
        if pid not in pages:
            pages[pid] = pager.empty(pid)
        pages[pid]["slots"][r["slot"]] = r["after"]
        pages[pid]["lsn"] = r["lsn"]
        last_lsn[pid] = r["lsn"]
    return {pid: (last_lsn[pid], pages[pid]["slots"]) for pid in pages}


def verify_recovery(directory, label):
    pf = pager.PageFile(os.path.join(directory, "pages.db"))
    expected = expected_state(directory)
    pids = set(range(pf.count())) | set(expected)
    failures = []
    for pid in sorted(pids):
        raw = pf.read_raw(pid)
        state = pager.verify(raw)
        if state == "torn":
            failures.append("page %d still torn after recovery" % pid)
            continue
        page = pager.decode(raw, pid)
        exp_lsn, exp_slots = expected.get(pid, (0, pager.empty(pid)["slots"]))
        if page["lsn"] != exp_lsn:
            failures.append("page %d lsn=%d expected %d" % (pid, page["lsn"], exp_lsn))
        if page["slots"] != exp_slots:
            failures.append("page %d slots=%s expected %s" % (pid, page["slots"], exp_slots))
    if failures:
        raise AssertionError("[%s] %s" % (label, "; ".join(failures)))
    print("PASS %-38s pages=%d" % (label, len(pids)))


def build(workload, directory):
    engine = Engine(directory)
    for step in workload:
        if step["op"] == "crash":
            break
        engine.apply(step)


# --------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------- #
def scenario_torn_flush(root):
    d = os.path.join(root, "torn")
    build(workload_with_checkpoint(), d)
    tear_page(d, 0)  # power loss halfway through page 0's write
    rc = run_recover(d)
    assert rc.returncode == 0, rc.stderr
    info = json.loads(rc.stdout)
    assert info["repaired_pages"] == [0] and info["undone"] == [2], info
    verify_recovery(d, "torn page + checkpoint")


def scenario_torn_then_undo_crash(root):
    d = os.path.join(root, "torn_undo_crash")
    build(workload_with_checkpoint(), d)
    tear_page(d, 0)
    rc = run_recover(d, crash_after_undo=1)
    assert rc.returncode == 75, rc.stderr
    # Half-rolled-back state must itself be consistent (CLR already durable).
    pf = pager.PageFile(os.path.join(d, "pages.db"))
    for pid in range(pf.count()):
        assert pager.verify(pf.read_raw(pid)) != "torn"
    rc = run_recover(d)  # resume
    assert rc.returncode == 0, rc.stderr
    info = json.loads(rc.stdout)
    assert info["undone"] == [2] and info["clrs"] == 1, info
    verify_recovery(d, "torn page, crash mid-undo, resume")


def scenario_repeated_undo_crashes(root):
    # For every possible crash point (1st, 2nd, 3rd CLR ...), build a fresh
    # crashed database, interrupt recovery there, then resume and verify.
    for n in (1, 2, 3, 4):
        d = os.path.join(root, "multi_undo_crash_%d" % n)
        build(workload_repeated_crashes(), d)  # loser tx7 owns 4 UPDATEs
        tear_page(d, 0)
        rc = run_recover(d, crash_after_undo=n)
        assert rc.returncode == 75, (n, rc.stderr)
        rc = run_recover(d)  # resume and finish the rollback
        assert rc.returncode == 0, rc.stderr
        run_recover(d)  # an extra run must be a no-op
        verify_recovery(d, "crash after CLR #%d then resume" % n)

    # Two crashes chained on the same database: crash after CLR 1, resume and
    # crash again after the next fresh CLR, then finish.
    d = os.path.join(root, "multi_undo_chain")
    build(workload_repeated_crashes(), d)
    tear_page(d, 0)
    assert run_recover(d, crash_after_undo=1).returncode == 75
    # On resume exactly 3 CLRs remain; interrupt after the first of those.
    assert run_recover(d, crash_after_undo=1).returncode == 75
    assert run_recover(d).returncode == 0
    verify_recovery(d, "two chained crashes inside undo")


def scenario_half_checkpoint(root):
    d = os.path.join(root, "half_ckpt")
    build(workload_two_checkpoints(), d)
    # Tear page 1 for real: cut its 256 bytes in half, invalidating its CRC.
    truncate_file(os.path.join(d, "pages.db"),
                  pager.PAGE_SIZE + pager.PAGE_SIZE // 2)
    # Keep the second CKPT_BEGIN but cut off its CKPT_END and everything
    # after it: analysis must ignore the incomplete checkpoint and fall back
    # to the first, complete one.
    begins = [r["lsn"] for r in Log(os.path.join(d, "wal.log")).records()
              if r["type"] == "CKPT_BEGIN"]
    truncate_file(os.path.join(d, "wal.log"), wal_offset_of_lsn(d, begins[-1]))
    rc = run_recover(d)
    assert rc.returncode == 0, rc.stderr
    info = json.loads(rc.stdout)
    assert info["checkpoint"] == begins[0], info
    assert info["repaired_pages"] == [1], info
    verify_recovery(d, "checkpoint CKPT_END torn away")


def scenario_log_tail_torn(root):
    d = os.path.join(root, "log_tail")
    build(workload_with_checkpoint(), d)
    tear_page(d, 0)
    path = os.path.join(d, "wal.log")
    size = os.path.getsize(path)
    truncate_file(path, size - 7)  # split the last log line mid-write
    rc = run_recover(d)
    assert rc.returncode == 0, rc.stderr
    verify_recovery(d, "WAL last record partially written")


def scenario_log_cut_at_record(root):
    d = os.path.join(root, "log_cut")
    build(workload_with_checkpoint(), d)
    tear_page(d, 0)
    # Cut right after tx3 COMMIT: its END is lost, tx2 UPDATE survives.
    commit = find_lsn(d, lambda r: r["type"] == "COMMIT" and r["tx"] == 3)
    truncate_file(os.path.join(d, "wal.log"), wal_offset_of_lsn(d, commit))
    rc = run_recover(d)
    assert rc.returncode == 0, rc.stderr
    verify_recovery(d, "WAL cut after COMMIT, END lost")


def scenario_page_file_truncated(root):
    d = os.path.join(root, "page_trunc")
    build(workload_no_checkpoint(), d)
    pf = pager.PageFile(os.path.join(d, "pages.db"))
    full = pf.count() * pager.PAGE_SIZE
    truncate_file(os.path.join(d, "pages.db"), full - pager.PAGE_SIZE // 2)
    rc = run_recover(d)
    assert rc.returncode == 0, rc.stderr
    verify_recovery(d, "page file cut mid-page")


def scenario_page_file_shrunk(root):
    d = os.path.join(root, "page_shrink")
    build(workload_no_checkpoint(), d)
    # Drop the last page entirely; redo recreates it, undo removes loser data.
    pf = pager.PageFile(os.path.join(d, "pages.db"))
    truncate_file(os.path.join(d, "pages.db"),
                  (pf.count() - 1) * pager.PAGE_SIZE)
    rc = run_recover(d)
    assert rc.returncode == 0, rc.stderr
    verify_recovery(d, "page file truncated to whole pages")


def scenario_no_checkpoint_torn(root):
    d = os.path.join(root, "no_ckpt")
    build(workload_no_checkpoint(), d)
    tear_page(d, 0)
    rc = run_recover(d)
    assert rc.returncode == 0, rc.stderr
    info = json.loads(rc.stdout)
    assert info["checkpoint"] == 0 and info["redo_start"] >= 1, info
    verify_recovery(d, "no checkpoint, torn page, redo from lsn 1")


def scenario_double_recovery_is_stable(root):
    d = os.path.join(root, "stable")
    build(workload_two_checkpoints(), d)
    assert run_recover(d).returncode == 0
    first = pager.PageFile(os.path.join(d, "pages.db"))
    snap = {i: first.read_raw(i) for i in range(first.count())}
    run_recover(d)
    second = pager.PageFile(os.path.join(d, "pages.db"))
    for i, raw in snap.items():
        assert second.read_raw(i) == raw, "page %d changed on re-recovery" % i
    verify_recovery(d, "repeated recovery byte-stable")


SCENARIOS = [
    scenario_torn_flush,
    scenario_torn_then_undo_crash,
    scenario_repeated_undo_crashes,
    scenario_half_checkpoint,
    scenario_log_tail_torn,
    scenario_log_cut_at_record,
    scenario_page_file_truncated,
    scenario_page_file_shrunk,
    scenario_no_checkpoint_torn,
    scenario_double_recovery_is_stable,
]


def main():
    root = tempfile.mkdtemp(prefix="crashsim-")
    try:
        for scenario in SCENARIOS:
            scenario(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("\nall %d crash scenarios verified" % len(SCENARIOS))


if __name__ == "__main__":
    main()
