"""Acceptance tests for the rewritten buffer pool.

Covers:
  * LRU-K victim order on a mixed hot-access / full-table-scan trace,
    including the exact evicted page sequence and the hit/miss counts.
  * Pin counts: pinned frames are never evicted; an unpinnable pool returns
    BUFFER_FULL immediately instead of waiting.
  * Write-ahead logging at every possible crash injection point, plus
    recovery completeness against an independent replay oracle.
  * Checkpoint redo_lsn advancement from the earliest dirty-frame rec_lsn.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import storage  # noqa: E402
from bufpool import BufferFull, BufferPool, Crash  # noqa: E402

BP = os.path.join(ROOT, "bp.py")
HOT_SCAN = os.path.join(ROOT, "examples", "hot_and_scan.json")
ALL_PINNED = os.path.join(ROOT, "examples", "all_pinned.json")


# --------------------------------------------------------------------------- #
# In-process fakes
# --------------------------------------------------------------------------- #
class FakeDisk:
    def __init__(self, npages=32):
        self.pages = {p: (0, ["", "", "", ""]) for p in range(npages)}
        self.written = []  # (pid, page_lsn) in write order

    def read(self, pid):
        lsn, slots = self.pages.get(pid, (0, ["", "", "", ""]))
        return lsn, list(slots)

    def write(self, pid, lsn, slots):
        self.written.append((pid, lsn))
        self.pages[pid] = (lsn, list(slots))


class FakeWal:
    def __init__(self):
        self.next_lsn = 1
        self.flushed = 0
        self.records = []

    def append(self, record):
        record = {"lsn": self.next_lsn, **record}
        self.next_lsn += 1
        self.records.append(record)
        return record["lsn"]

    def flush(self, upto=None):
        upto = self.next_lsn if upto is None else upto
        self.flushed = max(self.flushed, upto)


class WalOrderingDisk(FakeDisk):
    """Asserts the WAL-ahead rule on every data page write."""

    def __init__(self, wal, npages=32):
        super().__init__(npages)
        self.wal = wal

    def write(self, pid, lsn, slots):
        # The log record of this page's latest change must already be durable.
        assert self.wal.flushed >= lsn, (
            "WAL violation: page %s written at lsn %s but only %s is durable"
            % (pid, lsn, self.wal.flushed)
        )
        super().write(pid, lsn, slots)


# --------------------------------------------------------------------------- #
# Direct, in-process LRU-K / pin tests
# --------------------------------------------------------------------------- #
class LrukTests(unittest.TestCase):
    def test_scan_does_not_evict_hot_pages(self):
        disk, wal = FakeDisk(), FakeWal()
        pool = BufferPool(disk, wal, frames=3, k=2)
        for pid in (0, 1, 2, 0, 1, 2):  # hot pages get 2 accesses each
            pool.fetch(pid)
            pool.unpin(pid)
        # First scan page has to evict a frame to enter: all three frames are
        # in the infinite-distance group, and page 0's first access is oldest.
        # Every later scan page only evicts another one-access scan page;
        # once page 10 has two accesses it joins the finite-distance group and
        # pages 1, 2 (two accesses each) are never chosen while scan pages
        # with infinite distance exist.
        for pid in (10, 11, 12, 13):
            pool.fetch(pid)
            pool.unpin(pid)
        self.assertEqual(pool.stats["evictions"], [0, 10, 11, 12])
        self.assertTrue(all(p in pool.frames for p in (1, 2, 13)))
        self.assertEqual(pool.stats["hits"], 3)
        self.assertEqual(pool.stats["misses"], 7)

    def test_finite_distance_victim_order(self):
        disk, wal = FakeDisk(), FakeWal()
        pool = BufferPool(disk, wal, frames=3, k=2)
        # Each page ends with two accesses; page 0's second access is oldest,
        # so it has the largest backward K-distance and is evicted first.
        for pid in (0, 1, 2, 0, 1, 2, 0, 1, 2):
            pool.fetch(pid)
            pool.unpin(pid)
        pool.fetch(9)
        pool.unpin(9)
        self.assertEqual(pool.stats["evictions"], [0])
        # Page 9 has only one access (infinite distance), so it is evicted
        # before any page that already has two accesses.
        pool.fetch(8)
        pool.unpin(8)
        self.assertEqual(pool.stats["evictions"], [0, 9])

    def test_pinned_frames_are_protected(self):
        disk, wal = FakeDisk(), FakeWal()
        pool = BufferPool(disk, wal, frames=4, k=2)
        for pid in range(4):
            pool.fetch(pid)  # left pinned
        with self.assertRaises(BufferFull):
            pool.fetch(9)
        self.assertEqual(pool.stats["evictions"], [])
        # Nested pins need two unpins before the frame becomes evictable.
        pool.fetch(2)
        self.assertEqual(pool.frames[2].pins, 2)
        pool.unpin(2)
        self.assertEqual(pool.frames[2].pins, 1)
        with self.assertRaises(BufferFull):
            pool.fetch(9)
        pool.unpin(2)
        self.assertEqual(pool.frames[2].pins, 0)
        pool.fetch(9)  # page 2 now becomes the LRU-K victim
        self.assertEqual(pool.stats["evictions"], [2])

    def test_unpin_underflow_is_ignored(self):
        disk, wal = FakeDisk(), FakeWal()
        pool = BufferPool(disk, wal, frames=2, k=2)
        self.assertFalse(pool.unpin(7))
        pool.fetch(0)
        pool.unpin(0)
        self.assertFalse(pool.unpin(0))

    def test_wal_is_durable_before_every_data_write(self):
        wal = FakeWal()
        disk = WalOrderingDisk(wal)
        pool = BufferPool(disk, wal, frames=2, k=2)
        # Dirty frames get evicted; WalOrderingDisk asserts WAL-ahead on each.
        for pid in range(6):
            pool.modify(pid, 0, "v%d" % pid)
        pool.flush_some(10)
        self.assertTrue(disk.written)
        for _pid, lsn in disk.written:
            self.assertLessEqual(lsn, wal.flushed)

    def test_rec_lsn_resets_after_writeback(self):
        disk, wal = FakeDisk(), FakeWal()
        pool = BufferPool(disk, wal, frames=2, k=2)
        pool.modify(0, 0, "a")  # lsn 1
        pool.modify(0, 1, "b")  # lsn 2, rec_lsn stays 1
        self.assertEqual(pool.frames[0].rec_lsn, 1)
        pool.flush_some(10)
        self.assertIsNone(pool.frames[0].rec_lsn)
        pool.modify(0, 2, "c")  # lsn 3: re-dirtied, rec_lsn recomputed
        self.assertEqual(pool.frames[0].rec_lsn, 3)
        self.assertEqual(pool.checkpoint(), 3)

    def test_checkpoint_uses_earliest_dirty_rec_lsn(self):
        disk, wal = FakeDisk(), FakeWal()
        pool = BufferPool(disk, wal, frames=4, k=2)
        pool.modify(0, 0, "a")  # lsn 1, rec_lsn 1
        pool.modify(1, 0, "b")  # lsn 2, rec_lsn 2
        pool.modify(0, 1, "c")  # lsn 3; page 0 rec_lsn stays 1
        redo = pool.checkpoint()
        self.assertEqual(redo, 1)  # earliest first-dirty LSN, not latest lsn 3
        self.assertEqual(pool.stats["checkpoints"], [1])
        pool.flush_some(10)
        next_lsn = wal.next_lsn      # LSN the next checkpoint record will take
        redo = pool.checkpoint()     # nothing dirty now
        self.assertEqual(redo, next_lsn)


# --------------------------------------------------------------------------- #
# End-to-end trace acceptance via the bp.py CLI
# --------------------------------------------------------------------------- #
def run_cli(directory, trace, frames=4, k=2, crash_at_write=None):
    if isinstance(trace, str):  # path to an existing trace file
        path = trace
    else:
        path = os.path.join(directory, "trace.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(trace, fh)
    cmd = [sys.executable, BP, "run", directory, path,
           "--frames", str(frames), "--k", str(k)]
    if crash_at_write is not None:
        cmd += ["--crash-at-write", str(crash_at_write)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if not proc.stdout:
        raise AssertionError(
            "bp.py produced no output (rc=%s): %s" % (proc.returncode, proc.stderr))
    return proc.returncode, json.loads(proc.stdout)


def durable_records(directory):
    return storage.Wal(os.path.join(directory, "wal.log")).durable()


def assert_wal_invariant(testcase, directory):
    """Every page LSN on disk must be a durable WAL record for that page."""
    durable = durable_records(directory)
    by_lsn = {r["lsn"]: r for r in durable if "page" in r}
    disk = storage.Disk(os.path.join(directory, "data.db"))
    for pid in range(disk.count()):
        lsn, _slots = disk.read(pid)
        if lsn == 0:
            continue
        testcase.assertIn(lsn, by_lsn, "page lsn %s has no durable log" % lsn)
        testcase.assertEqual(by_lsn[lsn]["page"], pid)


def replay_oracle(directory):
    """Independently rebuild every page from the durable WAL."""
    durable = [r for r in durable_records(directory) if "page" in r]
    expected = {}
    for r in sorted(durable, key=lambda r: r["lsn"]):
        slots = expected.setdefault(r["page"], ["", "", "", ""])
        slots[r["slot"]] = r["value"]
    return expected


def assert_recovery_matches_oracle(testcase, directory):
    result = subprocess.run(
        [sys.executable, BP, "recover", directory],
        capture_output=True, text=True, check=True)
    recovered = json.loads(result.stdout)
    durable = durable_records(directory)
    checkpoints = [r["redo_lsn"] for r in durable
                   if r.get("type") == "checkpoint"]
    testcase.assertEqual(recovered["redo_lsn"],
                         checkpoints[-1] if checkpoints else 1)
    disk = storage.Disk(os.path.join(directory, "data.db"))
    expected = replay_oracle(directory)
    for pid, slots in expected.items():
        _lsn, on_disk = disk.read(pid)
        testcase.assertEqual(on_disk, slots, "page %s mismatch after recovery" % pid)
    # Pages the oracle never touched must be empty.
    for pid in range(disk.count()):
        if pid not in expected:
            _lsn, slots = disk.read(pid)
            testcase.assertEqual(slots, ["", "", "", ""])


class TraceAcceptanceTests(unittest.TestCase):
    def test_hot_and_scan_trace_hit_rate_and_exact_evictions(self):
        with tempfile.TemporaryDirectory() as d:
            code, out = run_cli(d, HOT_SCAN)
            self.assertEqual(code, 0)
            self.assertEqual(out["errors"], [])
            # Hot pages 0..2 survive all three scans; only scan pages leave.
            # From scan 2 on every scan page already has one history entry, so
            # each new scan round evicts that round's pages oldest-first.
            self.assertEqual(
                out["evictions"],
                [10, 11, 12, 13, 14, 15,
                 10, 11, 12, 13, 14, 15,
                 10, 11, 12, 13, 14])
            self.assertEqual(out["hits"], 19)
            self.assertEqual(out["misses"], 21)
            self.assertEqual(out["hits"] + out["misses"], 40)
            # Page 0 was evicted during a scan and reloaded before the writes,
            # so writing alpha re-dirties it with rec_lsn 1. At checkpoint
            # time frames 0 (rec 1) and 2 (rec 5) are still dirty; redo
            # advances to the earliest first-dirty LSN, i.e. 1.
            self.assertEqual(out["checkpoints"], [1])

    def test_all_pinned_returns_buffer_full_without_waiting(self):
        with tempfile.TemporaryDirectory() as d:
            code, out = run_cli(d, ALL_PINNED)
            self.assertEqual(code, 0)  # the trace continues after the error
            self.assertEqual(out["errors"], [{"step": 4, "error": "BUFFER_FULL"}])
            # After unpinning page 2 the retry loads page 9 and evicts page 2.
            self.assertEqual(out["evictions"], [2])
            # The failed fetch installs no frame, so it counts as neither hit
            # nor miss; the five successful loads (pages 0..3, then 9) are
            # all misses.
            self.assertEqual(out["hits"], 0)
            self.assertEqual(out["misses"], 5)

    def _write_heavy_trace(self):
        return [
            {"op": "write", "page": p, "slot": 0, "value": "w%d" % p}
            for p in range(6)
        ] + [
            {"op": "flush", "max": 2},
            {"op": "write", "page": 1, "slot": 1, "value": "second"},
            {"op": "write", "page": 4, "slot": 2, "value": "third"},
            {"op": "checkpoint"},
            {"op": "write", "page": 0, "slot": 3, "value": "fourth"},
            {"op": "commit"},
            {"op": "flush", "max": 3},
            {"op": "write", "page": 7, "slot": 0, "value": "late"},
            {"op": "write", "page": 8, "slot": 0, "value": "later"},
            {"op": "flush", "max": 10},
        ]

    def test_crash_at_every_write_keeps_wal_invariant_and_recovers(self):
        trace = self._write_heavy_trace()
        total_writes = self._count_writes(trace)
        self.assertGreater(total_writes, 1)
        baseline = self._baseline_state(trace)
        for n in range(1, total_writes + 2):  # include a point past the last write
            with tempfile.TemporaryDirectory() as d:
                code, out = run_cli(d, trace, crash_at_write=n)
                if n <= total_writes:
                    self.assertEqual(code, 75)
                    self.assertEqual(out["crashed_at_write"], n)
                else:
                    self.assertEqual(code, 0)
                assert_wal_invariant(self, d)
                assert_recovery_matches_oracle(self, d)
                # Recovery is idempotent: replaying again changes nothing.
                subprocess.run([sys.executable, BP, "recover", d],
                               capture_output=True, text=True, check=True)
                assert_wal_invariant(self, d)
        # The non-crashing run must end in the same state as the oracle.
        with tempfile.TemporaryDirectory() as d:
            run_cli(d, trace)
            assert_recovery_matches_oracle(self, d)
            self.assertEqual(self._on_disk_state(d), baseline)

    def test_crash_with_no_commit_still_recovers(self):
        # No commit and no checkpoint: durability comes only from eviction /
        # flush write-backs, which must force the WAL ahead of each page.
        trace = [
            {"op": "write", "page": p, "slot": 0, "value": "x%d" % p}
            for p in range(8)
        ] + [{"op": "flush", "max": 10}]
        total_writes = self._count_writes(trace)
        for n in range(1, total_writes + 1):
            with tempfile.TemporaryDirectory() as d:
                code, out = run_cli(d, trace, crash_at_write=n)
                self.assertEqual(code, 75)
                assert_wal_invariant(self, d)
                assert_recovery_matches_oracle(self, d)

    # ------------------------------------------------------------------ #
    def _count_writes(self, trace):
        """Run the trace in-process and count data page write-backs."""
        with tempfile.TemporaryDirectory() as d:
            disk = storage.Disk(os.path.join(d, "data.db"))
            wal = storage.Wal(os.path.join(d, "wal.log"))
            pool = BufferPool(disk, wal, frames=4, k=2)
            for op in trace:
                kind = op["op"]
                if kind == "get":
                    pool.fetch(op["page"])
                    pool.unpin(op["page"])
                elif kind == "write":
                    pool.modify(op["page"], op["slot"], op["value"])
                elif kind == "commit":
                    wal.flush()
                elif kind == "flush":
                    pool.flush_some(op.get("max", pool.capacity))
                elif kind == "checkpoint":
                    pool.checkpoint()
            return pool.writes

    def _baseline_state(self, trace):
        with tempfile.TemporaryDirectory() as d:
            run_cli(d, trace)
            return self._on_disk_state(d)

    @staticmethod
    def _on_disk_state(directory):
        disk = storage.Disk(os.path.join(directory, "data.db"))
        state = {}
        for pid in range(disk.count()):
            _lsn, slots = disk.read(pid)
            if any(slots):
                state[pid] = slots
        return state


if __name__ == "__main__":
    unittest.main()
