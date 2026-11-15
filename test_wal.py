#!/usr/bin/env python3
"""Acceptance tests for the checkpointed staging log.

Run with::

    python3 -m unittest -v test_wal

The tests cover the three requirements:

1. checkpoint-watermark truncation plus crash recovery replay;
2. record-level corruption handling and concurrent append/truncate;
3. segment lifecycle statistics, recovery point and batch consistency,
   including 20 injected crashes and two ``seg_size``/fault-order variants
   that must reach the same final state.
"""

import json
import hashlib
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import wal  # noqa: E402

# The test sandbox only permits writes below TMPDIR; fall back to the
# checkout when TMPDIR is unset.
_TEST_TMP = os.environ.get("TMPDIR") or HERE


def _records(prefix, count):
    return [prefix + str(i) for i in range(count)]


def _oracle(request):
    """Independent reference model derived from the README semantics.

    Returns ``(ingested_batches, ingested_record_count)``.  The oracle keeps
    only what the contract talks about: records with a seq, the checkpoint
    watermark, per-record damage, and a durable destination keyed by seq.
    Segments exist solely to resolve ``corrupt``'s ``(seg, pos)`` address,
    using the spec's truncation rule (reclaim only wholly committed
    segments).  The destination survives truncation, so committed records
    are never lost just because their segment was reclaimed.
    """
    seg_size = request.get("seg_size", 8)
    all_records = []
    segments = []
    watermark = 0
    seq = 0
    destination = {}  # seq -> batch

    for op in request.get("ops", []):
        kind = op.get("op")
        if kind == "append":
            for value in op.get("records", []):
                seq += 1
                record = {"seq": seq, "batch": op.get("batch"), "bad": False}
                all_records.append(record)
                if not segments or len(segments[-1]) >= seg_size:
                    segments.append([])
                segments[-1].append(record)
        elif kind == "checkpoint":
            watermark = seq
        elif kind == "truncate":
            segments = [
                segment
                for segment in segments
                if not all(r["seq"] <= watermark for r in segment)
            ]
        elif kind == "corrupt":
            target = None
            if op.get("seq") is not None:
                target = next(
                    (r for r in all_records if r["seq"] == int(op["seq"])), None
                )
            elif op.get("batch") is not None:
                matches = [r for r in all_records if r["batch"] == op["batch"]]
                index = int(op.get("index", op.get("pos", 0)))
                if index < 0:
                    index += len(matches)
                if 0 <= index < len(matches):
                    target = matches[index]
            else:
                seg = int(op.get("seg", 0))
                pos = int(op.get("pos", 0))
                if seg < 0:
                    seg += len(segments)
                if 0 <= seg < len(segments):
                    segment = segments[seg]
                    if pos < 0:
                        pos += len(segment)
                    if 0 <= pos < len(segment):
                        target = segment[pos]
            if target is not None:
                target["bad"] = True
        elif kind in ("ingest", "recover"):
            for record in all_records:
                if record["seq"] <= watermark and not record["bad"]:
                    destination.setdefault(record["seq"], record["batch"])
        # crash does not change durable state in this model.

    batches = sorted(set(destination.values()))
    return batches, len(destination)


class SegmentTruncationTests(unittest.TestCase):
    def test_straddling_tail_segment_is_retained(self):
        """Only wholly committed segments may be reclaimed."""
        log = wal.SegmentLog(seg_size=4)
        log.append(1, ["a", "b", "c"])     # seq 1-3
        log.checkpoint()                   # watermark = 3
        log.append(2, ["d", "e"])          # seq 4 in segment 0, seq 5 in segment 1
        removed = log.truncate()
        self.assertEqual(removed, 0)
        self.assertEqual(
            log.segments_summary(),
            [
                {"first": 1, "last": 4, "records": 4},
                {"first": 5, "last": 5, "records": 1},
            ],
        )
        stats = log.lifecycle_stats()
        self.assertEqual(stats["max_truncated_seq"], 0)
        self.assertLessEqual(stats["max_truncated_seq"], stats["checkpoint"])

    def test_fully_committed_segments_are_reclaimed(self):
        log = wal.SegmentLog(seg_size=2)
        log.append(1, ["a", "b"])          # seq 1-2
        log.append(2, ["c", "d"])          # seq 3-4
        log.checkpoint()                   # watermark = 4
        log.append(3, ["e"])               # seq 5, unprotected
        removed = log.truncate()
        self.assertEqual(removed, 2)
        self.assertEqual(log.segments_summary(), [{"first": 5, "last": 5, "records": 1}])
        stats = log.lifecycle_stats()
        self.assertEqual(stats["max_truncated_seq"], 4)
        self.assertLessEqual(stats["max_truncated_seq"], stats["checkpoint"])

    def test_truncate_before_ingest_does_not_lose_committed_batches(self):
        log = wal.SegmentLog(seg_size=2)
        log.append("b1", ["a", "b"])
        log.checkpoint()
        log.truncate()
        self.assertEqual(log.segments_summary(), [])
        log.ingest()
        self.assertEqual(log.result()["ingested"], ["b1"])

    def test_uncommitted_records_are_not_ingested(self):
        log = wal.SegmentLog(seg_size=4)
        log.append(1, ["a", "b"])
        log.checkpoint()
        log.append(2, ["c", "d"])
        log.ingest()
        self.assertEqual(log.result()["ingested"], [1])
        # The uncommitted tail is still in the staging log for later commit.
        self.assertEqual(log.segments_summary(), [{"first": 1, "last": 4, "records": 4}])
        log.checkpoint()
        log.ingest()
        self.assertEqual(log.result()["ingested"], [1, 2])

    def test_truncate_leaves_no_wholly_committed_segment(self):
        rng = random.Random(4321)
        for _ in range(60):
            log = wal.SegmentLog(seg_size=rng.randint(1, 5))
            for batch in range(1, 9):
                log.append(batch, _records("q%d_" % batch, rng.randint(1, 4)))
                if rng.random() < 0.5:
                    log.checkpoint()
                if rng.random() < 0.6:
                    log.truncate()
                    watermark = log.lifecycle_stats()["checkpoint"]
                    # Right after truncation no live segment may consist
                    # solely of committed records...
                    for segment in log.segments_summary():
                        self.assertGreater(
                            segment["last"], watermark,
                            "wholly committed segment survived truncation",
                        )
                    # ...and nothing above the watermark may have been lost.
                    self.assertLessEqual(
                        log.lifecycle_stats()["max_truncated_seq"], watermark
                    )
                self.assertLessEqual(
                    log.lifecycle_stats()["max_truncated_seq"],
                    log.lifecycle_stats()["checkpoint"],
                )


class CorruptionTests(unittest.TestCase):
    def test_corrupt_record_skipped_but_following_records_kept(self):
        log = wal.SegmentLog(seg_size=5)
        log.append(1, ["a", "b", "c", "d", "e"])
        log.checkpoint()
        self.assertTrue(log.corrupt(seg=0, pos=2))
        log.recover()
        # The damaged slot is dropped; its neighbours survive.
        self.assertEqual(log.result()["recovered"], 4)
        self.assertEqual(log.result()["ingested"], [1])

    def test_corruption_does_not_poison_later_batches(self):
        log = wal.SegmentLog(seg_size=4)
        log.append(1, ["a", "b", "c", "d"])
        log.append(2, ["e", "f"])
        log.checkpoint()
        log.corrupt(seg=0, pos=1)
        log.recover()
        self.assertEqual(log.result()["ingested"], [1, 2])

    def test_negative_indices_target_last_segment(self):
        log = wal.SegmentLog(seg_size=2)
        log.append(1, ["a", "b"])
        log.append(2, ["c"])
        log.checkpoint()
        self.assertTrue(log.corrupt(seg=-1, pos=-1))
        log.ingest()
        self.assertEqual(log.result()["recovered"], 2)

    def test_logical_corruption_after_truncation(self):
        """Logical addresses still work once the segment is gone."""
        log = wal.SegmentLog(seg_size=2)
        log.append(1, ["a", "b"])
        log.append(2, ["c", "d"])
        log.checkpoint()
        log.truncate()                 # both segments reclaimed
        self.assertEqual(log.segments_summary(), [])
        self.assertTrue(log.corrupt(batch=2, index=1))
        self.assertTrue(log.corrupt(seq=1))
        log.ingest()
        # seq 1 (damaged) and seq 4 (damaged) are skipped; 2 and 3 apply.
        self.assertEqual(log.result()["recovered"], 2)
        self.assertEqual(log.result()["ingested"], [1, 2])

    def test_physical_and_logical_addressing_agree_per_segment_size(self):
        """The same logical fault hits the same record under any layout."""
        for seg_size in (1, 2, 3, 8):
            log = wal.SegmentLog(seg_size)
            log.append(1, ["a", "b", "c"])
            log.append(2, ["d", "e"])
            log.checkpoint()
            self.assertTrue(log.corrupt(batch=2, index=0))
            log.ingest()
            self.assertEqual(log.segments_summary()[0]["first"], 1)
            self.assertEqual(log.result()["recovered"], 4, "seg_size=%d" % seg_size)

    def test_damaged_tail_record_is_never_ingested(self):
        log = wal.SegmentLog(seg_size=3)
        log.append(1, ["a", "b", "c"])
        log.corrupt(seg=0, pos=2)      # before any checkpoint
        log.checkpoint()
        log.ingest()
        self.assertEqual(log.result()["recovered"], 2)
        # Truncation then drops the damaged record without resurrecting it.
        log.truncate()
        log.ingest()
        log.recover()
        self.assertEqual(log.result()["recovered"], 2)

    def test_out_of_range_corruption_is_a_noop(self):
        log = wal.SegmentLog(seg_size=2)
        log.append(1, ["a"])
        self.assertFalse(log.corrupt(seg=5, pos=0))
        self.assertFalse(log.corrupt(seg=0, pos=99))
        self.assertFalse(log.corrupt(seg="x", pos="y"))


class CrashRecoveryTests(unittest.TestCase):
    def _workload(self, seg_size, seed):
        rng = random.Random(seed)
        log = wal.SegmentLog(seg_size=seg_size)
        batch = 0
        for _ in range(40):
            batch += 1
            log.append(batch, _records("b%d_" % batch, rng.randint(1, 6)))
            if rng.random() < 0.55:
                log.checkpoint()
            if rng.random() < 0.3:
                log.truncate()
            if rng.random() < 0.25:
                log.ingest()
            if rng.random() < 0.2:
                log.crash()
                log.recover()
        log.checkpoint()
        log.ingest()
        return log

    def test_twenty_injected_crashes_commit_without_loss(self):
        """20 crash/recover cycles must preserve every committed batch."""
        for crash_round in range(20):
            log = wal.SegmentLog(seg_size=3 + crash_round % 4)
            batch_ids = []
            for batch in range(1, 6):
                log.append(batch, _records("c%d_" % batch, batch % 3 + 1))
                log.checkpoint()
                log.ingest()
                batch_ids.append(batch)
                log.crash()
                log.recover()
                self.assertEqual(sorted(log.result()["ingested"]), batch_ids)
            # A truncation between commits must not disturb the ledger.
            log.truncate()
            log.crash()
            log.recover()
            self.assertEqual(sorted(log.result()["ingested"]), batch_ids)
            self.assertEqual(log.result()["recovered"], len(log._dest_seqs))

    def test_recovery_is_idempotent(self):
        log = wal.SegmentLog(seg_size=2)
        log.append(1, ["a", "b"])
        log.checkpoint()
        first = log.recover()
        second = log.recover()
        self.assertEqual(first, 2)
        self.assertEqual(second, 0)
        self.assertEqual(log.result()["recovered"], 2)

    def test_recovery_point_is_last_consistent_checkpoint(self):
        log = wal.SegmentLog(seg_size=2)
        log.append(1, ["a", "b"])
        log.checkpoint()               # consistent point: seq 2
        log.append(2, ["c", "d"])      # not committed
        log.crash()
        log.recover()
        self.assertEqual(log.result()["ingested"], [1])
        self.assertEqual(log.result()["recovered"], 2)
        self.assertEqual(log.lifecycle_stats()["checkpoint"], 2)

    def test_randomized_scenarios_are_stable(self):
        for seed in range(10):
            log = self._workload(seg_size=1 + seed % 5, seed=seed)
            result = log.result()
            # The workload ends with a checkpoint + ingest, so every batch
            # that was appended is committed and must be in the warehouse.
            batch_count = log.lifecycle_stats()["batches_ingested"]
            self.assertEqual(result["ingested"], list(range(1, batch_count + 1)))
            self.assertEqual(result["recovered"], len(log._dest_seqs))
            self.assertLessEqual(
                log.lifecycle_stats()["max_truncated_seq"],
                log.lifecycle_stats()["checkpoint"],
            )


class ConcurrencyTests(unittest.TestCase):
    def test_concurrent_append_and_truncate(self):
        log = wal.SegmentLog(seg_size=4)
        errors = []
        stop = threading.Event()

        def appender(tag):
            try:
                for i in range(300):
                    log.append(("%s-%d" % (tag, i)), ["x", "y"])
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        def truncator():
            try:
                while not stop.is_set():
                    log.checkpoint()
                    log.truncate()
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=appender, args=("a",)),
                   threading.Thread(target=appender, args=("b",)),
                   threading.Thread(target=truncator)]
        threads[-1].start()
        for thread in threads[:-1]:
            thread.start()
        for thread in threads[:-1]:
            thread.join()
        stop.set()
        threads[-1].join()
        self.assertEqual(errors, [])
        # Commit and drain whatever the concurrent writers produced.
        log.checkpoint()
        log.truncate()
        log.ingest()
        stats = log.lifecycle_stats()
        self.assertEqual(stats["records_appended"], 1200)
        self.assertEqual(stats["records_ingested"], 1200)
        self.assertEqual(log._pending_committed, [])
        # No duplicates or gaps in the destination ledger.
        seqs = sorted(log._dest_seqs)
        self.assertEqual(seqs, list(range(1, 1201)))


class CliAndDeterminismTests(unittest.TestCase):
    def _run_cli(self, request):
        directory = tempfile.mkdtemp(prefix="wal-test-", dir=_TEST_TMP)
        self.addCleanup(shutil.rmtree, directory, True)
        input_path = os.path.join(directory, "input.json")
        output_path = os.path.join(directory, "output.json")
        with open(input_path, "w", encoding="utf-8") as handle:
            json.dump(request, handle)
        completed = subprocess.run(
            [sys.executable, os.path.join(HERE, "wal.py"), input_path, output_path],
            capture_output=True,
            text=True,
            cwd=directory,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        with open(output_path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def _fault_permutation_variant(self, seg_size, fault_order):
        """Place the same logical faults in a caller-chosen order.

        Crash, corruption and recovery are order-insensitive with respect
        to the durable terminal state: damage is recorded in the log, a
        crash only discards volatile state, and recovery replays up to the
        last checkpoint.  Any permutation anchored at the same logical
        position must therefore converge.
        """
        faults = {
            "corrupt": {"op": "corrupt", "batch": 2, "index": 1},
            "crash": {"op": "crash"},
            "recover": {"op": "recover"},
        }
        ops = [
            {"op": "append", "batch": 1, "records": ["a", "b", "c", "d", "e"]},
            {"op": "checkpoint"},
            {"op": "ingest"},
            {"op": "append", "batch": 2, "records": ["f", "g", "h"]},
        ]
        ops.extend(faults[name] for name in fault_order)
        ops.extend(
            [
                {"op": "checkpoint"},
                {"op": "truncate"},
                {"op": "append", "batch": 3, "records": ["i", "j"]},
                {"op": "checkpoint"},
                {"op": "truncate"},
                {"op": "ingest"},
            ]
        )
        return {"seg_size": seg_size, "ops": ops}

    def test_cli_output_shape(self):
        request = {
            "seg_size": 2,
            "ops": [
                {"op": "append", "batch": 7, "records": ["a", "b", "c"]},
                {"op": "checkpoint"},
                {"op": "ingest"},
            ],
        }
        result = self._run_cli(request)
        self.assertEqual(set(result), {"ingested", "segments", "recovered", "digest"})
        self.assertEqual(result["ingested"], [7])
        self.assertEqual(
            result["segments"],
            [
                {"first": 1, "last": 2, "records": 2},
                {"first": 3, "last": 3, "records": 1},
            ],
        )
        self.assertEqual(result["recovered"], 3)
        self.assertEqual(len(result["digest"]), 40)

    def test_two_segment_sizes_and_out_of_order_faults_converge(self):
        fault_orders = [
            ("corrupt", "crash", "recover"),
            ("crash", "corrupt", "recover"),
            ("crash", "recover", "corrupt"),
        ]
        for fault_order in fault_orders:
            baseline = None
            for seg_size in (2, 8):
                result = self._run_cli(
                    self._fault_permutation_variant(seg_size, fault_order)
                )
                if baseline is None:
                    baseline = result
                    self.assertEqual(result["ingested"], [1, 2, 3])
                    # 10 records appended, 1 damaged -> 9 reach the warehouse.
                    self.assertEqual(result["recovered"], 9)
                else:
                    # Different physical layout, identical terminal state.
                    self.assertEqual(result["ingested"], baseline["ingested"])
                    self.assertEqual(result["recovered"], baseline["recovered"])
                    self.assertEqual(result["digest"], baseline["digest"])
            # And the fault ordering itself does not change the outcome.
            reference = self._run_cli(
                self._fault_permutation_variant(2, ("corrupt", "crash", "recover"))
            )
            self.assertEqual(baseline, reference)

    def test_lifecycle_stats_and_recovery_point_are_reported(self):
        request = {
            "seg_size": 2,
            "include_stats": True,
            "ops": [
                {"op": "append", "batch": 1, "records": ["a", "b"]},
                {"op": "checkpoint"},
                {"op": "truncate"},
                {"op": "append", "batch": 2, "records": ["c"]},
                {"op": "checkpoint"},
                {"op": "ingest"},
            ],
        }
        result = self._run_cli(request)
        # The documented keys remain intact.
        self.assertTrue({"ingested", "segments", "recovered", "digest"} <= set(result))
        self.assertEqual(result["recovery_point"], 3)
        lifecycle = result["lifecycle"]
        self.assertEqual(lifecycle["segments_created"], 2)
        self.assertEqual(lifecycle["segments_truncated"], 1)
        self.assertEqual(lifecycle["records_appended"], 3)
        self.assertEqual(lifecycle["records_truncated"], 2)
        self.assertEqual(lifecycle["records_ingested"], 3)
        self.assertLessEqual(lifecycle["max_truncated_seq"], result["recovery_point"])

    def test_mixed_type_batch_ids_are_stable(self):
        """Batch ids do not have to be mutually comparable."""
        request = {
            "seg_size": 2,
            "ops": [
                {"op": "append", "batch": "b", "records": ["x"]},
                {"op": "append", "batch": 10, "records": ["y"]},
                {"op": "append", "batch": 2, "records": ["z"]},
                {"op": "checkpoint"},
                {"op": "ingest"},
            ],
        }
        result = wal.run(request)
        self.assertEqual(len(result["ingested"]), 3)
        self.assertEqual(result["recovered"], 3)
        # Deterministic across runs (including digest).
        self.assertEqual(result, wal.run(request))

    def test_empty_operation_list(self):
        result = wal.run({"seg_size": 4, "ops": []})
        self.assertEqual(
            result,
            {"ingested": [], "segments": [], "recovered": 0,
             "digest": hashlib.sha1(b"[]").hexdigest()},
        )

    def test_malformed_input_fails_cleanly(self):
        directory = tempfile.mkdtemp(prefix="wal-bad-", dir=_TEST_TMP)
        self.addCleanup(shutil.rmtree, directory, True)
        cases = ["not json", "[]", '{"seg_size": 0, "ops": []}',
                 '{"seg_size": 2, "ops": [{"op": "append", "records": 7}]}']
        for index, payload in enumerate(cases):
            input_path = os.path.join(directory, "bad%d.json" % index)
            output_path = os.path.join(directory, "bad%d.out.json" % index)
            with open(input_path, "w", encoding="utf-8") as handle:
                handle.write(payload)
            completed = subprocess.run(
                [sys.executable, os.path.join(HERE, "wal.py"), input_path, output_path],
                capture_output=True,
                text=True,
                cwd=directory,
            )
            self.assertEqual(completed.returncode, 1, payload)
            self.assertIn("wal:", completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)
            self.assertFalse(os.path.exists(output_path))

    def test_cli_stats_flag(self):
        request = {
            "seg_size": 2,
            "ops": [
                {"op": "append", "batch": 1, "records": ["a", "b"]},
                {"op": "checkpoint"},
                {"op": "ingest"},
            ],
        }
        directory = tempfile.mkdtemp(prefix="wal-stats-", dir=_TEST_TMP)
        self.addCleanup(shutil.rmtree, directory, True)
        input_path = os.path.join(directory, "in.json")
        output_path = os.path.join(directory, "out.json")
        with open(input_path, "w", encoding="utf-8") as handle:
            json.dump(request, handle)
        completed = subprocess.run(
            [sys.executable, os.path.join(HERE, "wal.py"), "--stats",
             input_path, output_path],
            capture_output=True, text=True, cwd=directory,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        with open(output_path, "r", encoding="utf-8") as handle:
            result = json.load(handle)
        self.assertEqual(result["recovery_point"], 2)
        self.assertIn("lifecycle", result)

    def test_fuzz_against_oracle(self):
        """Random operation soup must match the independent reference model."""
        rng = random.Random(2024)
        for _ in range(120):
            seg_size = rng.choice([1, 2, 3, 4, 8])
            ops = []
            batch = 0
            for _ in range(rng.randint(1, 18)):
                choice = rng.random()
                if choice < 0.45:
                    batch += 1
                    ops.append({
                        "op": "append", "batch": batch,
                        "records": _records("f%d_" % batch, rng.randint(1, 4)),
                    })
                elif choice < 0.65:
                    ops.append({"op": "checkpoint"})
                elif choice < 0.78:
                    ops.append({"op": "truncate"})
                elif choice < 0.88:
                    # Mix physical and logical addressing.
                    if rng.random() < 0.5:
                        ops.append({"op": "corrupt", "seg": rng.randint(-2, 2),
                                    "pos": rng.randint(-2, 2)})
                    else:
                        ops.append({"op": "corrupt", "batch": rng.randint(1, max(batch, 1)),
                                    "index": rng.randint(0, 2)})
                elif choice < 0.94:
                    ops.append({"op": "crash"})
                else:
                    ops.append({"op": "recover"})
            request = {"seg_size": seg_size, "ops": ops}
            expected_batches, expected_records = _oracle(request)
            result = wal.run(request)
            self.assertEqual(
                sorted(result["ingested"]), sorted(expected_batches),
                "batches diverged for %r" % (request,),
            )
            self.assertEqual(
                result["recovered"], expected_records,
                "record count diverged for %r" % (request,),
            )
            self.assertEqual(result, wal.run(request))

    def test_recovery_summary_matches_terminal_state(self):
        """For every scenario the summary equals an oracle computation."""
        rng = random.Random(77)
        for _ in range(15):
            seg_size = rng.choice([1, 2, 3, 8])
            ops = []
            batch = 0
            for _ in range(12):
                batch += 1
                ops.append({"op": "append", "batch": batch,
                            "records": _records("x%d_" % batch, rng.randint(1, 3))})
                if rng.random() < 0.6:
                    ops.append({"op": "checkpoint"})
                if rng.random() < 0.4:
                    ops.append({"op": "truncate"})
                if rng.random() < 0.3:
                    # Physical addressing exercises "where is that record
                    # now" after truncation has shifted the segment list.
                    ops.append({"op": "corrupt", "seg": rng.randint(0, 2),
                                "pos": rng.randint(0, 2)})
                if rng.random() < 0.4:
                    ops.append({"op": "ingest"})
                if rng.random() < 0.3:
                    ops.append({"op": "crash"})
                    ops.append({"op": "recover"})
            ops.append({"op": "checkpoint"})
            ops.append({"op": "ingest"})
            request = {"seg_size": seg_size, "ops": ops}

            expected_batches, expected_records = _oracle(request)
            result = self._run_cli(request)
            self.assertEqual(sorted(result["ingested"]), sorted(expected_batches))
            self.assertEqual(result["recovered"], expected_records)
            # Re-running the identical request is deterministic.
            self.assertEqual(result, self._run_cli(request))


if __name__ == "__main__":
    unittest.main(verbosity=2)

