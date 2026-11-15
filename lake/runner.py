"""Apply an op stream to the layered engine under a chosen concurrency mode."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .engine import Engine
from .model import Config
from .scheduler import Scheduler
from .workload import ReferenceDB


@dataclass
class RunResult:
    mode: str
    config_name: str
    seed: int
    engine: Engine
    scheduler: Optional[Scheduler]
    reference: ReferenceDB
    decisions: List[Dict[str, Any]]
    read_results: List[Any]
    read_mismatches: List[Dict[str, Any]]
    summary: Dict[str, Any]
    compaction_reports: List[Dict[str, Any]]


def _config_presets() -> Dict[str, Config]:
    return {
        # Two layer-count / capacity shapes used by the acceptance matrix.
        "compact4": Config(
            levels=4,
            file_caps=(4, 4, 8, 16),
            memtable_records=4,
            target_file_records=24,
            max_concurrent_compactions=2,
        ),
        "deep5": Config(
            levels=5,
            file_caps=(3, 4, 6, 10, 20),
            memtable_records=3,
            target_file_records=20,
            max_concurrent_compactions=3,
        ),
    }


def config_preset(name: str) -> Config:
    return _config_presets()[name]


def run_workload(
    ops: List[Dict[str, Any]],
    config: Config,
    seed: int,
    mode: str = "concurrent",
    strict_version: bool = False,
) -> RunResult:
    """Execute ``ops``.

    mode:
      * ``concurrent``: seeded scheduler may plan several compactions per pump;
      * ``serial``: one compaction planned and committed per pump, giving the
        deterministic serial reference implementation.
    """
    assert mode in ("concurrent", "serial")
    engine = Engine.create(config)
    serial_cfg = config
    if mode == "serial":
        serial_cfg = Config(
            levels=config.levels,
            file_caps=config.file_caps,
            memtable_records=config.memtable_records,
            target_file_records=config.target_file_records,
            max_concurrent_compactions=1,
            max_compaction_inputs=config.max_compaction_inputs,
        )
        engine = Engine.create(serial_cfg)
    scheduler = Scheduler(engine, seed=seed, strict_version=strict_version)
    reference = ReferenceDB()
    read_results: List[Any] = []
    read_mismatches: List[Dict[str, Any]] = []
    op_index = 0
    for op in ops:
        kind = op["op"]
        if mode == "concurrent":
            scheduler.note_op()
        if kind == "put":
            engine.put(op["k"], op["v"])
        elif kind == "del":
            engine.delete(op["k"])
        elif kind == "snap":
            engine.snapshot(op.get("snap"))
        elif kind == "release":
            engine.release(op["snap"])
        elif kind == "read":
            snap_id = op.get("snap")
            got = engine.read(op["k"], snap_id=snap_id)
            read_results.append(got)
        else:
            raise ValueError("unknown op %r" % kind)
        reference.apply(op)
        if kind == "read":
            expected = reference.reads[-1]
            if got != expected:
                read_mismatches.append(
                    {
                        "op_index": op_index,
                        "op": op,
                        "expected": expected,
                        "got": got,
                    }
                )
        # Drain pressure continuously so snapshot windows overlap compactions.
        # Both modes fully settle per op; only the per-pump concurrency differs.
        if mode == "concurrent":
            scheduler.settle()
        else:
            _drain_serial(engine)
        op_index += 1
    engine.flush()
    if mode == "concurrent":
        scheduler.settle()
    else:
        _drain_serial(engine)
    final_kv = engine.manifest.live_key_values()
    ref_kv = reference.final_kv()
    assert read_results == reference.reads or read_mismatches, (
        "read divergence not detected"
    )
    decisions = scheduler.decisions
    compaction_reports = [
        d["report"] for d in decisions if d["kind"] == "commit"
    ]
    summary = _summarize(
        engine,
        scheduler,
        final_kv,
        ref_kv,
        read_mismatches,
        compaction_reports,
    )
    return RunResult(
        mode=mode,
        config_name="",
        seed=seed,
        engine=engine,
        scheduler=scheduler,
        reference=reference,
        decisions=decisions,
        read_results=read_results,
        read_mismatches=read_mismatches,
        summary=summary,
        compaction_reports=compaction_reports,
    )


def replay_workload(
    ops: List[Dict[str, Any]],
    config: Config,
    seed: int,
    recorded: List[Dict[str, Any]],
    strict_version: bool = False,
) -> RunResult:
    """Re-run an op stream while re-enacting a recorded decision log.

    The original run settles after every op, so the recorded pumps are grouped
    by their position between ops.  We walk the log with a cursor and replay
    exactly the pump entries that the original run produced at that point.
    Every regenerated plan and commit report is asserted equal, so concurrent
    compaction decisions are fully replayable rather than re-randomised.
    """
    engine = Engine.create(config)
    scheduler = Scheduler(engine, seed=seed, strict_version=strict_version)
    reference = ReferenceDB()
    read_results: List[Any] = []
    read_mismatches: List[Dict[str, Any]] = []
    cursor = 0
    total = len(recorded)
    op_index = 0

    def consume_pumps_for_op(current_op: int) -> None:
        """Replay pumps whose recorded after_op matches this op boundary."""
        nonlocal cursor
        while cursor < total:
            if recorded[cursor]["kind"] != "pump_start":
                cursor += 1
                continue
            if recorded[cursor]["after_op"] != current_op:
                break
            end = cursor
            pump_id = recorded[end]["pump"]
            while end < total and not (
                recorded[end]["kind"] == "pump_end"
                and recorded[end]["pump"] == pump_id
            ):
                end += 1
            end += 1  # include pump_end
            scheduler.replay(recorded[cursor:end])
            cursor = end

    for op in ops:
        kind = op["op"]
        scheduler.note_op()
        got = None
        if kind == "put":
            engine.put(op["k"], op["v"])
        elif kind == "del":
            engine.delete(op["k"])
        elif kind == "snap":
            engine.snapshot(op.get("snap"))
        elif kind == "release":
            engine.release(op["snap"])
        elif kind == "read":
            got = engine.read(op["k"], snap_id=op.get("snap"))
            read_results.append(got)
        else:
            raise ValueError("unknown op %r" % kind)
        reference.apply(op)
        if kind == "read":
            expected = reference.reads[-1]
            if got != expected:
                read_mismatches.append(
                    {"op_index": op_index, "op": op,
                     "expected": expected, "got": got}
                )
        consume_pumps_for_op(op_index)
        op_index += 1
    engine.flush()
    # Pumps recorded after the final op (final settle) carry after_op == n-1.
    consume_pumps_for_op(len(ops) - 1)
    assert cursor == total, "unconsumed decision entries: %d/%d" % (
        cursor,
        total,
    )
    assert scheduler.decisions == recorded, "decision replay diverged"
    final_kv = engine.manifest.live_key_values()
    ref_kv = reference.final_kv()
    compaction_reports = [
        d["report"] for d in scheduler.decisions if d["kind"] == "commit"
    ]
    summary = _summarize(
        engine,
        scheduler,
        final_kv,
        ref_kv,
        read_mismatches,
        compaction_reports,
    )
    return RunResult(
        mode="replay",
        config_name="",
        seed=seed,
        engine=engine,
        scheduler=scheduler,
        reference=reference,
        decisions=scheduler.decisions,
        read_results=read_results,
        read_mismatches=read_mismatches,
        summary=summary,
        compaction_reports=compaction_reports,
    )


def _drain_serial(engine: Engine) -> None:
    """Single-threaded reference: repeatedly pick one plan and commit it."""
    for _ in range(10000):
        planner = engine.planner()
        plan = planner.pick()
        if plan is None:
            return
        engine.execute(plan)


def _summarize(
    engine: Engine,
    scheduler: Optional[Scheduler],
    final_kv: Dict[str, Any],
    ref_kv: Dict[str, Any],
    read_mismatches: List[Dict[str, Any]],
    compaction_reports: List[Dict[str, Any]],
) -> Dict[str, Any]:
    m = engine.manifest
    stats = engine.stats.to_dict()
    summary = {
        "state_digest": m.digest(),
        "final_kv": final_kv,
        "reference_kv": ref_kv,
        "kv_match": final_kv == ref_kv,
        "read_mismatches": len(read_mismatches),
        "watermark": m.watermark,
        "watermark_monotonic": m.watermark >= 0,
        "level_file_counts": [len(lv) for lv in m.levels],
        "physical_bytes": m.physical_bytes(),
        "live_logical_bytes": m.live_logical_bytes(),
        "space_amplification": round(engine.space_amplification(), 6),
        "write_amplification": round(engine.write_amplification(), 6),
        "compactions": stats["compactions_completed"],
        "stats": stats,
        "open_snapshots": len(m.snapshots),
        "pinned_orphan_files": len(m.orphan_files),
    }
    if scheduler is not None:
        summary["pumps"] = scheduler.pumps
        summary["aborts"] = sum(
            1 for d in scheduler.decisions if d["kind"] == "abort"
        )
        summary["decisions"] = len(scheduler.decisions)
    else:
        summary["pumps"] = 0
        summary["aborts"] = 0
        summary["decisions"] = 0
    return summary
