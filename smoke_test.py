"""End-to-end tests for the reserve.py command line: journaling and recovery.

Run with ``python3 smoke_test.py``.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESERVE = ROOT / "reserve.py"


def run(input_path, output_path, journal, *extra, cwd=None):
    return subprocess.run(
        [sys.executable, str(RESERVE), str(input_path), str(output_path),
         "--journal", str(journal), *extra],
        capture_output=True, text=True, cwd=cwd or str(ROOT),
    )


EVENTS = [
    {"seq": 1, "at": 0, "op": "reserve", "key": "a", "start": 0, "end": 10, "ttl": 5},
    {"seq": 2, "at": 0, "op": "reserve", "key": "a", "start": 10, "end": 20, "ttl": 8},
    {"seq": 3, "at": 1, "op": "publish", "key": "a", "lease": 1, "epoch": 1},
    {"seq": 4, "at": 2, "op": "reserve", "key": "a", "start": 0, "end": 5, "ttl": 4},
    {"seq": 5, "at": 3, "op": "reserve", "key": "b", "start": 0, "end": 5, "ttl": 9},
    {"seq": 6, "at": 6, "op": "reserve", "key": "a", "start": 0, "end": 5, "ttl": 9},
    {"seq": 7, "at": 6, "op": "publish", "key": "a", "lease": 6, "epoch": 2},
    {"seq": 8, "at": 7, "op": "release", "key": "a", "lease": 1, "epoch": 1},
    {"seq": 9, "at": 8, "op": "tick"},
]


def read_json(path):
    return json.loads(Path(path).read_text())


class CliTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def write_input(self, name, events):
        path = self.dir / name
        path.write_text(json.dumps({"events": events}))
        return path

    def baseline(self, events=EVENTS):
        inp = self.write_input("all.json", events)
        out = self.dir / "all.out.json"
        journal = self.dir / "all.jsonl"
        result = run(inp, out, journal)
        self.assertEqual(result.returncode, 0, result.stderr)
        return read_json(out), journal.read_bytes()

    def test_clean_run_and_restart_are_identical(self):
        expected, raw_journal = self.baseline()
        self.assertEqual(len(raw_journal.splitlines()), len(EVENTS))
        # Re-running the same batch is a no-op replay of committed events.
        inp = self.write_input("again.json", EVENTS)
        out = self.dir / "again.out.json"
        journal = self.dir / "again.jsonl"
        journal.write_bytes(raw_journal)
        result = run(inp, out, journal)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read_json(out), expected)
        self.assertEqual(journal.read_bytes(), raw_journal)

    def test_cut_after_journal_then_resume(self):
        expected, _ = self.baseline()
        out = self.dir / "cut.out.json"
        journal = self.dir / "cut.jsonl"
        cut = 4
        first = run(
            self.write_input("cut.json", EVENTS), out, journal,
            "--cut-after-journal", str(cut),
        )
        self.assertEqual(first.returncode, 75, first.stderr)
        self.assertFalse(out.exists(), "no output before the crash point")
        lines = journal.read_text().splitlines()
        self.assertEqual(len(lines), cut)
        # Restart from the same journal with the remaining events.
        rest = self.write_input("rest.json", EVENTS[cut:])
        second = run(rest, out, journal)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(read_json(out), expected)
        self.assertEqual(len(journal.read_text().splitlines()), len(EVENTS))

    def test_partial_trailing_record_is_discarded(self):
        expected, raw = self.baseline()
        journal = self.dir / "partial.jsonl"
        journal.write_bytes(raw + b'{"seq": 10, "at": 9, "op": "tick"')
        out = self.dir / "partial.out.json"
        result = run(self.write_input("empty.json", []), out, journal)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read_json(out), expected)
        # The fragment is replaced, not kept.
        self.assertTrue(journal.read_bytes().endswith(b"\n"))
        self.assertEqual(len(journal.read_text().splitlines()), len(EVENTS))

    def test_partial_record_with_new_pending_events(self):
        expected, raw = self.baseline()
        journal = self.dir / "tail.jsonl"
        journal.write_bytes(raw + b'{"seq": 9, "at": 8, "op": "')
        out = self.dir / "tail.out.json"
        result = run(self.dir / "tail.in.json" and self.write_input("tail.in.json", []), out, journal)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read_json(out), expected)

    def test_mid_journal_corruption_rejects_and_preserves_files(self):
        _, raw = self.baseline()
        lines = raw.splitlines(keepends=True)
        damaged = lines[0] + b"corrupted\n" + b"".join(lines[1:])
        journal = self.dir / "damaged.jsonl"
        journal.write_bytes(damaged)
        out = self.dir / "damaged.out.json"
        out.write_text("sentinel\n")
        result = run(self.write_input("more.json", [{"seq": 12, "at": 9, "op": "tick"}]), out, journal)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(journal.read_bytes(), damaged)
        self.assertEqual(out.read_text(), "sentinel\n")

    def test_gap_and_conflicting_duplicate_are_rejected(self):
        inp = self.write_input("gap.json", [
            {"seq": 1, "at": 0, "op": "tick"},
            {"seq": 3, "at": 0, "op": "tick"},
        ])
        out = self.dir / "gap.out.json"
        journal = self.dir / "gap.jsonl"
        result = run(inp, out, journal)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(out.exists())
        self.assertFalse(journal.exists())

        # Same seq, different content in one batch.
        inp = self.write_input("dup.json", [
            {"seq": 1, "at": 0, "op": "tick"},
            {"seq": 1, "at": 1, "op": "tick"},
        ])
        result = run(inp, out, journal)
        self.assertEqual(result.returncode, 2)

        # Same seq, different content against the committed journal.
        committed = self.write_input("c1.json", [{"seq": 1, "at": 0, "op": "tick"}])
        self.assertEqual(run(committed, out, journal).returncode, 0)
        before = journal.read_bytes()
        out_before = out.read_bytes()
        clash = self.write_input("c2.json", [{"seq": 1, "at": 5, "op": "tick"}])
        result = run(clash, out, journal)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(journal.read_bytes(), before)
        self.assertEqual(out.read_bytes(), out_before)

    def test_identical_duplicate_reuses_original_result(self):
        first = self.write_input("d1.json", [
            {"seq": 1, "at": 0, "op": "reserve", "key": "k", "start": 0, "end": 5, "ttl": 9},
        ])
        out = self.dir / "d.out.json"
        journal = self.dir / "d.jsonl"
        self.assertEqual(run(first, out, journal).returncode, 0)
        again = self.write_input("d2.json", [
            {"seq": 1, "at": 0, "op": "reserve", "key": "k", "start": 0, "end": 5, "ttl": 9},
            {"seq": 2, "at": 1, "op": "tick"},
        ])
        self.assertEqual(run(again, out, journal).returncode, 0)
        outcome = read_json(out)
        self.assertEqual([r["seq"] for r in outcome["results"]], [1, 2])
        self.assertEqual(outcome["results"][0]["status"], "reserved")
        self.assertEqual(len(journal.read_text().splitlines()), 2)

    def test_out_of_order_batch_is_accepted_in_seq_order(self):
        expected, _ = self.baseline()
        out = self.dir / "ooo.out.json"
        journal = self.dir / "ooo.jsonl"
        shuffled = list(EVENTS)
        shuffled[0], shuffled[4] = shuffled[4], shuffled[0]
        result = run(self.write_input("ooo.json", shuffled), out, journal)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read_json(out), expected)

    def test_logical_time_regression_rejected(self):
        inp = self.write_input("time.json", [
            {"seq": 1, "at": 5, "op": "tick"},
            {"seq": 2, "at": 4, "op": "tick"},
        ])
        out = self.dir / "time.out.json"
        journal = self.dir / "time.jsonl"
        result = run(inp, out, journal)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(out.exists())

    def test_invalid_reserve_rejected(self):
        inp = self.write_input("bad.json", [
            {"seq": 1, "at": 0, "op": "reserve", "key": "k", "start": 5, "end": 5, "ttl": 1},
        ])
        out = self.dir / "bad.out.json"
        journal = self.dir / "bad.jsonl"
        self.assertEqual(run(inp, out, journal).returncode, 2)
        self.assertFalse(out.exists())
        self.assertFalse(journal.exists())

    def test_published_exactly_once_after_fault_recovery(self):
        expected, _ = self.baseline()
        out = self.dir / "f.out.json"
        journal = self.dir / "f.jsonl"
        first = run(
            self.write_input("f1.json", EVENTS[:3]), out, journal,
            "--cut-after-journal", "2",
        )
        self.assertEqual(first.returncode, 75)
        # Crash again, this time after journaling seq 3, still no output.
        second = run(self.write_input("f2.json", EVENTS[2:]), out, journal,
                     "--cut-after-journal", "3")
        self.assertEqual(second.returncode, 75)
        self.assertFalse(out.exists())
        # Skipping the uncommitted seq 4 is a gap and must be rejected.
        gapped = run(self.write_input("f2b.json", EVENTS[4:]), out, journal)
        self.assertEqual(gapped.returncode, 2)
        self.assertEqual(journal.read_text().count("\n"), 3)
        third = run(self.write_input("f3.json", EVENTS[3:]), out, journal)
        self.assertEqual(third.returncode, 0, third.stderr)
        outcome = read_json(out)
        self.assertEqual(outcome, expected)
        self.assertEqual(len(outcome["published"]), len(
            {(p["key"], p["lease"]) for p in outcome["published"]}
        ))

    def test_cut_at_already_committed_seq_still_crashes(self):
        expected, _ = self.baseline()
        out = self.dir / "late.out.json"
        journal = self.dir / "late.jsonl"
        self.assertEqual(
            run(self.write_input("late1.json", EVENTS[:3]), out, journal).returncode, 0
        )
        before_journal = journal.read_bytes()
        before_output = out.read_bytes()
        result = run(
            self.write_input("late2.json", EVENTS[3:]), out, journal,
            "--cut-after-journal", "2",
        )
        self.assertEqual(result.returncode, 75)
        self.assertEqual(journal.read_bytes(), before_journal)
        self.assertEqual(out.read_bytes(), before_output)

    def test_recovery_with_out_of_order_resubmission_and_old_seqs(self):
        expected, _ = self.baseline()
        out = self.dir / "ooo2.out.json"
        journal = self.dir / "ooo2.jsonl"
        first = run(
            self.write_input("o1.json", EVENTS[:4]), out, journal,
            "--cut-after-journal", "3",
        )
        self.assertEqual(first.returncode, 75)
        # The scheduler replays seq 4, resubmits the rest out of order, and
        # also replays an already committed seq with identical content.
        replay = [EVENTS[3], EVENTS[5], EVENTS[4]] + EVENTS[0:1] + EVENTS[6:]
        second = run(self.write_input("o2.json", replay), out, journal)
        self.assertEqual(second.returncode, 0, second.stderr)
        outcome = read_json(out)
        self.assertEqual(outcome, expected)
        self.assertEqual([r["seq"] for r in outcome["results"]], list(range(1, 10)))
        self.assertEqual(outcome["epochs"], expected["epochs"])
        self.assertEqual(len(journal.read_text().splitlines()), len(EVENTS))

    def test_output_replaced_atomically(self):
        out = self.dir / "atomic.out.json"
        out.write_text("old\n")
        journal = self.dir / "atomic.jsonl"
        inp = self.write_input("atomic.json", EVENTS[:1])
        self.assertEqual(run(inp, out, journal).returncode, 0)
        self.assertNotEqual(out.read_text(), "old\n")
        leftovers = [p.name for p in self.dir.iterdir() if p.suffix == ".tmp"]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)