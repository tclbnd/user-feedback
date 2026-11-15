"""Deterministic concurrent compaction scheduler with replayable decisions.

Real coordinators plan against a manifest snapshot and later validate before
commit.  We model that without threads: each pump round plans against the
current manifest, the seeded RNG decides how many free slots are filled and
in which order queued plans are committed, and every decision is appended to
an ordered log.  Replaying the log reproduces every plan, abort and commit
bit for bit, which is what the acceptance check requires.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .engine import CompactionConflict, Engine
from .planner import CompactionPlan, Planner


@dataclass
class Scheduler:
    engine: Engine
    seed: int
    strict_version: bool = False
    rng: random.Random = field(init=False, default=None)
    busy: set = field(default_factory=set)
    queued: List[CompactionPlan] = field(default_factory=list)
    decisions: List[Dict[str, Any]] = field(default_factory=list)
    pumps: int = 0
    # Index of the last applied workload op, stamped onto every decision so a
    # replay can align pumps with the exact same state evolution point.
    op_counter: int = -1

    def note_op(self) -> None:
        self.op_counter += 1

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)

    @property
    def stats(self):
        return self.engine.stats

    # --------------------------------------------------------------- decisions
    def _log(self, kind: str, **payload: Any) -> Dict[str, Any]:
        entry = {
            "pump": self.pumps,
            "kind": kind,
            "state_version": self.engine.manifest.state_version,
            "after_op": self.op_counter,
        }
        entry.update(payload)
        self.decisions.append(entry)
        return entry

    def _planner(self) -> Planner:
        return Planner(
            self.engine.manifest,
            busy=set(self.busy),
            next_plan_id=self._next_plan_cursor(),
        )

    def _next_plan_cursor(self) -> int:
        # Plan ids are per scheduler; continue numbering across pumps.
        return max((p.plan_id + 1 for p in self.queued), default=0)

    def _plan_more(self, fill: int) -> None:
        # One planner per pump round: its busy set grows as plans are reserved,
        # so subsequent picks never select files an earlier pick already owns.
        planner = self._planner()
        for _ in range(fill):
            plan = planner.pick()
            if plan is None:
                self._log("no_candidate")
                break
            self.queued.append(plan)
            planner.busy.update(plan.input_files)
            self.busy.update(plan.input_files)
            self._log(
                "plan",
                plan=plan.to_dict(),
                queued=len(self.queued),
                fill=fill,
            )

    def _commit_queue(self) -> None:
        # Deterministic shuffle models out-of-order completion of jobs that ran
        # concurrently; the RNG is seeded so the order is replayable.
        order = list(range(len(self.queued)))
        self.rng.shuffle(order)
        queued = self.queued
        for idx in order:
            plan = queued[idx]
            try:
                report = self.engine.execute(
                    plan, strict_version=self.strict_version
                )
            except CompactionConflict as exc:
                self.stats.compactions_aborted += 1
                self._log("abort", plan_id=plan.plan_id, reason=str(exc))
                continue
            self._log("commit", plan_id=plan.plan_id, report=report)
        # All reservations end with the pump round; aborted work is replanned
        # against fresh state on the next round.
        self.busy.clear()
        self.queued = []

    def pump(self) -> List[Dict[str, Any]]:
        self.pumps += 1
        # Flush first so the planner sees exactly the same file set on replay.
        self.engine.flush()
        slots = self.engine.manifest.config.max_concurrent_compactions
        # Seeded choice: sometimes fill all slots (parallel planning), sometimes
        # only one, exercising both serial and concurrent decision paths.
        fill = self.rng.randint(1, max(1, slots))
        self._log(
            "pump_start",
            fill=fill,
            busy=len(self.busy),
            l0_files=len(self.engine.manifest.levels[0]),
        )
        self._plan_more(fill)
        if self.queued:
            self._commit_queue()
        self._log(
            "pump_end",
            watermark=self.engine.manifest.watermark,
            physical_bytes=self.engine.manifest.physical_bytes(),
            level_file_counts=[
                len(lv) for lv in self.engine.manifest.levels
            ],
        )
        return [d for d in self.decisions if d.get("pump") == self.pumps]

    def settle(self, max_pumps: int = 10000) -> None:
        """Run pumps until the planner has no compactable candidate."""
        for _ in range(max_pumps):
            planner = Planner(
                self.engine.manifest,
                busy=set(self.busy),
                next_plan_id=self._next_plan_cursor(),
            )
            if planner.pick() is None and not self.queued and not self.busy:
                return
            self.pump()
        raise RuntimeError("settle() did not converge")

    # ----------------------------------------------------------------- replay
    def replay(self, recorded: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Replay a recorded decision log on this equivalent fresh scheduler.

        Each recorded plan is regenerated through the same deterministic
        planner and compared field by field; commit order, abort reasons and
        pump-end state are re-enacted and asserted.  Any divergence raises
        ``AssertionError`` so nondeterminism cannot pass silently.
        """
        replayed: List[Dict[str, Any]] = []
        i = 0
        n = len(recorded)
        while i < n:
            entry = recorded[i]
            assert entry["kind"] == "pump_start", entry
            self.pumps += 1
            # Mirror Scheduler.pump(): persist the memtable before planning so
            # the replay engine observes the identical flushed file set.
            self.engine.flush()
            replayed.append(
                self._mirror(
                    entry,
                    fill=entry["fill"],
                    busy=entry["busy"],
                    l0_files=entry["l0_files"],
                )
            )
            i += 1
            planner = Planner(
                self.engine.manifest,
                busy=set(self.busy),
                next_plan_id=self._next_plan_cursor(),
            )
            while i < n and recorded[i]["kind"] in ("plan", "no_candidate"):
                pe = recorded[i]
                if pe["kind"] == "no_candidate":
                    replayed.append(self._mirror(pe))
                    i += 1
                    break
                plan = planner.pick()
                assert plan is not None, pe
                assert plan.to_dict() == pe["plan"], (
                    "plan replay mismatch\nrecorded=%r\nactual=%r"
                    % (pe["plan"], plan.to_dict())
                )
                self.queued.append(plan)
                self.busy.update(plan.input_files)
                planner.busy = set(self.busy)
                replayed.append(
                    self._mirror(
                        pe,
                        plan=plan.to_dict(),
                        queued=pe["queued"],
                        fill=pe["fill"],
                    )
                )
                i += 1
            by_id = {p.plan_id: p for p in self.queued}
            while i < n and recorded[i]["kind"] in ("commit", "abort"):
                ce = recorded[i]
                plan = by_id[ce["plan_id"]]
                if ce["kind"] == "commit":
                    report = self.engine.execute(
                        plan, strict_version=self.strict_version
                    )
                    assert report == ce["report"], (
                        "commit replay mismatch\nrecorded=%r\nactual=%r"
                        % (ce["report"], report)
                    )
                    replayed.append(
                        self._mirror(ce, plan_id=plan.plan_id, report=report)
                    )
                else:
                    try:
                        self.engine.execute(
                            plan, strict_version=self.strict_version
                        )
                    except CompactionConflict as exc:
                        assert str(exc) == ce["reason"], (
                            "abort reason mismatch %r != %r"
                            % (str(exc), ce["reason"])
                        )
                        self.stats.compactions_aborted += 1
                        replayed.append(
                            self._mirror(
                                ce, plan_id=plan.plan_id, reason=str(exc)
                            )
                        )
                    else:
                        raise AssertionError("recorded abort committed on replay")
                i += 1
            self.busy.clear()
            self.queued = []
            assert i < n and recorded[i]["kind"] == "pump_end", recorded[i]
            end = recorded[i]
            mirrored = self._mirror(
                end,
                watermark=self.engine.manifest.watermark,
                physical_bytes=self.engine.manifest.physical_bytes(),
                level_file_counts=[
                    len(lv) for lv in self.engine.manifest.levels
                ],
            )
            assert mirrored == end, (
                "pump_end replay mismatch\nrecorded=%r\nactual=%r"
                % (end, mirrored)
            )
            replayed.append(mirrored)
            i += 1
        assert len(replayed) == len(recorded)
        assert replayed == recorded
        return replayed

    def _mirror(self, recorded: Dict[str, Any], **payload: Any) -> Dict[str, Any]:
        entry = {
            "pump": recorded["pump"],
            "kind": recorded["kind"],
            "state_version": self.engine.manifest.state_version,
            "after_op": recorded["after_op"],
        }
        entry.update(payload)
        self.decisions.append(entry)
        return entry
