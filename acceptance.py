#!/usr/bin/env python3
"""End-to-end acceptance demo for the staging-log implementation.

This is a thin, readable wrapper around the same engine used by ``wal.py``.
It reproduces the acceptance scenarios and prints a short report:

1. 20 injected crashes: every committed batch survives; the truncation
   watermark never passes the checkpoint; the damaged segment's good prefix
   (and suffix) survives recovery.
2. The same workload replayed with two segment sizes and several fault
   orderings: the warehouse terminal state and recovery summary agree.
3. Segment lifecycle statistics and the recovery point are reported.

Exit code is 0 only when every check passes.
"""

from __future__ import annotations

import json

import wal

FAILURES = []


def check(condition, message):
    if not condition:
        FAILURES.append(message)
        print("  FAIL  " + message)
    return condition


def _batch_records(batch, count):
    return ["b%d-r%d" % (batch, i) for i in range(count)]


def scenario_crashes(rounds=20):
    """20 crash/recover cycles with interleaved truncation and corruption."""
    print("scenario 1: %d injected crashes" % rounds)
    before = len(FAILURES)
    for index in range(rounds):
        seg_size = 2 + index % 4
        log = wal.SegmentLog(seg_size)
        committed = []
        for batch in range(1, 6):
            records = _batch_records(batch, 1 + batch % 3)
            log.append(batch, records)
            if index % 2 and len(records) > 1:
                # Damage one record *before* the batch is committed.  Its
                # surviving records are still enough for the batch to be
                # ingested, so the committed batch must not be lost.
                log.corrupt(batch=batch, index=0)
            log.checkpoint()
            log.ingest()
            committed.append(batch)
            log.crash()
            log.recover()
            # Every committed batch must still be in the warehouse.
            check(
                sorted(log.result()["ingested"]) == committed,
                "round %d batch %d: committed batch lost after crash" % (index, batch),
            )
            check(
                log.lifecycle_stats()["max_truncated_seq"]
                <= log.lifecycle_stats()["checkpoint"],
                "round %d batch %d: truncation passed the checkpoint" % (index, batch),
            )
        # A crash right after truncation must not disturb the ledger.
        log.truncate()
        log.crash()
        log.recover()
        check(
            sorted(log.result()["ingested"]) == committed,
            "round %d: truncate + crash lost committed batches" % index,
        )
        check(
            log.lifecycle_stats()["max_truncated_seq"]
            <= log.lifecycle_stats()["checkpoint"],
            "round %d: truncation watermark exceeded checkpoint" % index,
        )
    print("  %d rounds, %d failure(s)" % (rounds, len(FAILURES) - before))


def scenario_corruption_prefix():
    """A damaged record keeps the good records before and after it."""
    print("scenario 2: damaged record keeps its neighbours")
    before = len(FAILURES)
    log = wal.SegmentLog(seg_size=8)
    log.append(1, ["r%d" % i for i in range(8)])
    log.checkpoint()
    log.corrupt(seg=0, pos=3)
    log.recover()
    result = log.result()
    check(result["recovered"] == 7,
          "expected 7 good records, got %d" % result["recovered"])
    check(result["ingested"] == [1],
          "the batch with one damaged record should still be ingested")
    # The segment stays addressable: record-level damage does not shift the
    # offsets of following records.
    check(result["segments"] == [{"first": 1, "last": 8, "records": 8}],
          "damaged record should not shift segment offsets")
    print("  recovered=%d segments=%s" % (result["recovered"], result["segments"]))
    print("  %d failure(s)" % (len(FAILURES) - before))


def _workload(seg_size, fault_order):
    """Same logical workload; faults placed in ``fault_order``."""
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
    ops.extend([
        {"op": "checkpoint"},
        {"op": "truncate"},
        {"op": "append", "batch": 3, "records": ["i", "j"]},
        {"op": "checkpoint"},
        {"op": "truncate"},
        {"op": "ingest"},
    ])
    return {"seg_size": seg_size, "ops": ops}


def scenario_convergence():
    """Two segment sizes x several fault orderings must agree."""
    print("scenario 3: 2 segment sizes x fault orderings converge")
    before = len(FAILURES)
    orders = [
        ("corrupt", "crash", "recover"),
        ("crash", "corrupt", "recover"),
        ("crash", "recover", "corrupt"),
    ]
    results = {}
    for seg_size in (2, 8):
        for order in orders:
            results[(seg_size, order)] = wal.run(_workload(seg_size, order))
    reference = results[(2, orders[0])]
    for key, result in results.items():
        check(
            result["ingested"] == reference["ingested"]
            and result["recovered"] == reference["recovered"]
            and result["digest"] == reference["digest"],
            "seg_size=%s order=%s diverged from reference" % key,
        )
    print("  reference ingested=%s recovered=%d digest=%s"
          % (reference["ingested"], reference["recovered"], reference["digest"][:12]))
    print("  %d failure(s)" % (len(FAILURES) - before))


def scenario_stats():
    print("scenario 4: lifecycle statistics and recovery point")
    before = len(FAILURES)
    log = wal.SegmentLog(seg_size=2)
    log.append(1, ["a", "b"])
    log.checkpoint()
    log.truncate()
    log.append(2, ["c"])
    log.checkpoint()
    log.ingest()
    result = log.result(include_stats=True)
    lifecycle = result["lifecycle"]
    print("  " + json.dumps(
        {"recovery_point": result["recovery_point"], "lifecycle": lifecycle},
        sort_keys=True,
    ))
    check(result["recovery_point"] == 3,
          "recovery point should be the last checkpoint watermark")
    check(lifecycle["segments_created"] == 2, "expected 2 created segments")
    check(lifecycle["segments_truncated"] == 1, "expected 1 truncated segment")
    check(lifecycle["records_ingested"] == 3, "expected 3 ingested records")
    check(lifecycle["max_truncated_seq"] <= result["recovery_point"],
          "truncation watermark exceeded recovery point")
    print("  %d failure(s)" % (len(FAILURES) - before))


def main():
    scenario_crashes()
    scenario_corruption_prefix()
    scenario_convergence()
    scenario_stats()
    if FAILURES:
        print("\n%d check(s) failed" % len(FAILURES))
        return 1
    print("\nall acceptance checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
