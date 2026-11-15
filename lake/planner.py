"""Compaction file-group selection.

Selection policy (deterministic, no randomness):

1. A level is *over capacity* once it holds more files than its configured
   cap.  The most over-capacity level is chosen first; L0 wins ties because
   its range-overlapping files are the most expensive to read.
2. At L0 the oldest files are taken until the level is back at its cap.
   At Ln (n>=1) the source file with the smallest overlap against the next
   level is picked first, and every overlapping next-level file is pulled
   into the same group so the level invariant (disjoint ranges) is restored.
3. The candidate score records the key-range overlap ratio and estimated
   write amplification.  Candidates that touch files already compacted by a
   concurrent job are skipped rather than raced against.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .manifest import Manifest
from .model import FileMeta


@dataclass
class CompactionPlan:
    plan_id: int
    source_level: int
    target_level: int
    input_files: Tuple[int, ...]
    min_key: str
    max_key: str
    pressure: float
    overlap_ratio: float
    estimated_input_bytes: int
    estimated_output_records: int
    state_version: int
    reason: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "source_level": self.source_level,
            "target_level": self.target_level,
            "input_files": list(self.input_files),
            "min_key": self.min_key,
            "max_key": self.max_key,
            "pressure": round(self.pressure, 6),
            "overlap_ratio": round(self.overlap_ratio, 6),
            "estimated_input_bytes": self.estimated_input_bytes,
            "estimated_output_records": self.estimated_output_records,
            "state_version": self.state_version,
            "reason": self.reason,
        }


@dataclass
class Planner:
    manifest: Manifest
    # Ids of files currently owned by in-flight compactions.
    busy: Set[int] = field(default_factory=set)
    next_plan_id: int = 0

    def _alloc_plan_id(self) -> int:
        pid = self.next_plan_id
        self.next_plan_id += 1
        return pid

    # ------------------------------------------------------------------ policy
    def level_pressure(self, level: int) -> float:
        cap = self.manifest.config.file_caps[level]
        return len(self.manifest.levels[level]) / float(cap)

    def _overlaps_next_level(self, level: int, files: List[FileMeta]) -> bool:
        # Range invariants can be violated against ANY deeper level when the
        # levels in between are empty, so check all levels below, not just the
        # immediately adjacent one.
        for deeper in range(level + 1, self.manifest.config.levels):
            for f in files:
                for t in self.manifest.levels[deeper]:
                    if t.file_id in self.busy:
                        continue
                    if t.overlaps(f.min_key, f.max_key):
                        return True
        return False

    def _source_level(self) -> Optional[int]:
        best: Optional[Tuple[float, int]] = None
        for level in range(self.manifest.config.levels - 1):
            files = self.manifest.levels[level]
            available = [f for f in files if f.file_id not in self.busy]
            if not available:
                continue
            pressure = len(files) / float(self.manifest.config.file_caps[level])
            # Reaching capacity triggers compaction; any level holding more
            # than one file, overlapping a deeper level, or still carrying
            # tombstones keeps coalescing so garbage can reach the bottom and
            # be physically eliminated.
            has_tombstone = any(
                any(r.is_tombstone for r in f.records) for f in files
            )
            triggered = (
                pressure >= 1.0
                or len(files) >= 2
                or self._overlaps_next_level(level, files)
                or has_tombstone
            )
            if not triggered:
                continue
            # Boost pressure for range violations so they are repaired before
            # ordinary capacity-driven work.
            if self._overlaps_next_level(level, files):
                pressure += 10.0
            # L0 wins equal-pressure ties (negative level in tuple ordering).
            key = (pressure, -level)
            if best is None or key > best:
                best = (pressure, level)
        return None if best is None else best[1]

    def _pick_l0_group(self, files: List[FileMeta]) -> List[FileMeta]:
        cap = self.manifest.config.file_caps[0]
        # ``files`` is already busy-filtered.  Take the oldest files up to the
        # cap so two plans planned in the same pump round never share an L0
        # input: the second planner call only sees files not reserved yet.
        ordered = sorted(files, key=lambda f: f.file_id)
        take = min(len(ordered), cap)
        return ordered[:take]

    def _pick_seed(self, source_files: List[FileMeta],
                   target_files: List[FileMeta]) -> FileMeta:
        """Pick the source file whose range overlaps the fewest target bytes."""
        def score(f: FileMeta) -> Tuple[float, int, str]:
            overlap_bytes = 0
            for t in target_files:
                if t.overlaps(f.min_key, f.max_key):
                    overlap_bytes += t.logical_bytes
            ratio = overlap_bytes / max(1, f.logical_bytes)
            return (ratio, f.file_id, f.min_key)

        return min(source_files, key=score)

    def _expand_targets(self, min_key: str, max_key: str,
                        target_files: List[FileMeta]) -> List[FileMeta]:
        # Fixed-point expansion: targets may extend the covered range, which can
        # pull in further overlapping files.  Bounded by the global input guard.
        chosen: List[FileMeta] = []
        chosen_ids: Set[int] = set()
        changed = True
        guard = self.manifest.config.max_compaction_inputs
        while changed:
            changed = False
            for t in target_files:
                if t.file_id in chosen_ids:
                    continue
                if t.overlaps(min_key, max_key):
                    chosen.append(t)
                    chosen_ids.add(t.file_id)
                    min_key = min(min_key, t.min_key)
                    max_key = max(max_key, t.max_key)
                    changed = True
                    if len(chosen_ids) >= guard:
                        break
        chosen.sort(key=lambda f: (f.min_key, f.file_id))
        return chosen

    def _busy_target_collision(self, target_level: int,
                               min_key: str, max_key: str) -> bool:
        """True if the range overlaps a target file reserved by another job.

        Committing an output that overlaps a concurrently-compacted target file
        would break the disjoint-range invariant, so such a plan is deferred to
        the next pump round instead of being aborted late.
        """
        for t in self.manifest.levels[target_level]:
            if t.file_id in self.busy and t.overlaps(min_key, max_key):
                return True
        return False

    def pick(self) -> Optional[CompactionPlan]:
        source = self._source_level()
        if source is None:
            return None
        target = source + 1
        source_files = [
            f
            for f in self.manifest.levels[source]
            if f.file_id not in self.busy
        ]
        if not source_files:
            return None
        target_files = [
            f
            for f in self.manifest.levels[target]
            if f.file_id not in self.busy
        ]
        if source == 0:
            group = self._pick_l0_group(source_files)
        else:
            seed = self._pick_seed(source_files, target_files)
            group = [seed]
        min_key = min(f.min_key for f in group)
        max_key = max(f.max_key for f in group)
        target_group = self._expand_targets(min_key, max_key, target_files)
        all_inputs = group + target_group
        if len(all_inputs) > self.manifest.config.max_compaction_inputs:
            all_inputs = all_inputs[: self.manifest.config.max_compaction_inputs]
        min_key = min(f.min_key for f in all_inputs)
        max_key = max(f.max_key for f in all_inputs)
        if self._busy_target_collision(target, min_key, max_key):
            # Defer: another in-flight job owns the overlapping target range.
            return None
        source_bytes = sum(f.logical_bytes for f in group)
        target_bytes = sum(f.logical_bytes for f in target_group)
        input_bytes = source_bytes + target_bytes
        # Distinct-key estimate is exact after a merge, but cheap to estimate
        # here for the replayable decision record.
        keys: Set[str] = set()
        records = 0
        for f in all_inputs:
            records += len(f.records)
            keys.update(r.key for r in f.records)
        overlap_ratio = (
            target_bytes / source_bytes if source_bytes else 0.0
        )
        reason = (
            "l0-fifo-drain" if source == 0 else "min-overlap-range-group"
        )
        return CompactionPlan(
            plan_id=self._alloc_plan_id(),
            source_level=source,
            target_level=target,
            input_files=tuple(f.file_id for f in all_inputs),
            min_key=min_key,
            max_key=max_key,
            pressure=self.level_pressure(source),
            overlap_ratio=overlap_ratio,
            estimated_input_bytes=input_bytes,
            estimated_output_records=len(keys),
            state_version=self.manifest.state_version,
            reason=reason,
        )
