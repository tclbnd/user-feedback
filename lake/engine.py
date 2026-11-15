"""LSM-style lake engine: writes, flushes, merges, snapshot reads and stats."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .manifest import Manifest, Snapshot, WatermarkRegression
from .model import Config, FileMeta, Record, TOMBSTONE, newer
from .planner import CompactionPlan, Planner


class CompactionConflict(RuntimeError):
    """A replay-time conflict: the plan inputs are no longer compactable."""


@dataclass
class Stats:
    puts: int = 0
    deletes: int = 0
    flushes: int = 0
    compactions_started: int = 0
    compactions_completed: int = 0
    compactions_aborted: int = 0
    records_input: int = 0
    records_output: int = 0
    duplicates_dropped: int = 0
    tombstones_dropped: int = 0
    bytes_written: int = 0
    bytes_ingested: int = 0
    bytes_reclaimed: int = 0
    files_reclaimed: int = 0
    max_watermark: int = 0
    snapshot_creates: int = 0
    snapshot_releases: int = 0
    point_lookups: int = 0
    files_scanned: int = 0

    def to_dict(self) -> Dict[str, int]:
        return dict(self.__dict__)


@dataclass
class Engine:
    manifest: Manifest
    stats: Stats = field(default_factory=Stats)
    _memtable: List[Record] = field(default_factory=list)
    _write_index: int = 0

    @staticmethod
    def create(config: Optional[Config] = None) -> "Engine":
        return Engine(manifest=Manifest(config=config or Config()))

    # ------------------------------------------------------------------ writes
    def _next_seq(self) -> int:
        self.manifest.seq += 1
        return self.manifest.seq

    def put(self, key: str, value: Any, ts: Optional[int] = None) -> Record:
        self.stats.puts += 1
        seq = self._next_seq()
        rec = Record(
            key=key,
            seq=seq,
            ts=self.manifest.seq if ts is None else ts,
            write_index=self._write_index,
            value=value,
        )
        self._write_index += 1
        self._memtable.append(rec)
        self._maybe_flush()
        return rec

    def delete(self, key: str, ts: Optional[int] = None) -> Record:
        self.stats.deletes += 1
        seq = self._next_seq()
        rec = Record(
            key=key,
            seq=seq,
            ts=self.manifest.seq if ts is None else ts,
            write_index=self._write_index,
            value=TOMBSTONE,
        )
        self._write_index += 1
        self._memtable.append(rec)
        self._maybe_flush()
        return rec

    def flush(self) -> Optional[FileMeta]:
        if not self._memtable:
            return None
        records = sorted(
            self._memtable, key=lambda r: (r.key, -r.seq, -r.ts, -r.write_index)
        )
        fid = self.manifest.alloc_file_id()
        f = FileMeta(
            file_id=fid,
            level=0,
            records=records,
            origin="flush",
        )
        self.manifest.add_file(0, f)
        self._memtable = []
        self.stats.flushes += 1
        self.stats.bytes_ingested += f.logical_bytes
        self.stats.bytes_written += f.physical_bytes
        self.manifest.advance_watermark()
        return f

    def _maybe_flush(self) -> None:
        if len(self._memtable) >= self.manifest.config.memtable_records:
            self.flush()

    # --------------------------------------------------------------- snapshots
    def snapshot(self, snap_id: Optional[int] = None) -> Snapshot:
        self.flush()
        snap = self.manifest.create_snapshot(snap_id=snap_id)
        self.stats.snapshot_creates += 1
        return snap

    def release(self, snap_id: int) -> bool:
        ok = self.manifest.release_snapshot(snap_id)
        if ok:
            self.stats.snapshot_releases += 1
        return ok

    # --------------------------------------------------------------- compaction
    def planner(self) -> Planner:
        return Planner(self.manifest)

    def validate_plan(self, plan: CompactionPlan, strict_version: bool = False) -> None:
        """Re-validate a plan right before execution.

        ``strict_version`` models a pessimistic coordinator that aborts any plan
        planned against an older manifest version.  The default optimistic
        validation only aborts on actual input conflicts, which is what real
        concurrent compaction coordinators can safely commit.
        """
        if strict_version and plan.state_version != self.manifest.state_version:
            raise CompactionConflict(
                "plan %d stale: state_version %d != %d"
                % (plan.plan_id, plan.state_version, self.manifest.state_version)
            )
        live_ids = {f.file_id for f in self.manifest.all_files()}
        missing = [i for i in plan.input_files if i not in live_ids]
        if missing:
            raise CompactionConflict(
                "plan %d inputs missing/obsolete: %r" % (plan.plan_id, missing)
            )
        levels_used = {
            f.level for f in self.manifest.all_files()
            if f.file_id in set(plan.input_files)
        }
        allowed = {plan.source_level, plan.target_level}
        if not levels_used or not levels_used <= allowed:
            raise CompactionConflict(
                "plan %d inputs span unexpected levels %r"
                % (plan.plan_id, sorted(levels_used))
            )
        if plan.source_level not in levels_used:
            raise CompactionConflict(
                "plan %d has no source-level input" % plan.plan_id
            )

    def _merge_inputs(
        self, files: List[FileMeta]
    ) -> Tuple[List[Record], int, int]:
        """Merge sorted files and淘汰旧值 by (seq, ts, write_index)."""
        per_key: Dict[str, Record] = {}
        total_input = 0
        for f in files:
            total_input += len(f.records)
            for r in f.records:
                cur = per_key.get(r.key)
                if cur is None or newer(r, cur):
                    per_key[r.key] = r
        duplicates = total_input - len(per_key)
        return (
            [per_key[k] for k in sorted(per_key)],
            total_input,
            duplicates,
        )

    def _drop_bottom_tombstones(
        self, records: List[Record], bottom: bool
    ) -> Tuple[List[Record], int]:
        if not bottom:
            return records, 0
        oldest = self.manifest.oldest_snapshot_seq()
        kept: List[Record] = []
        dropped = 0
        for r in records:
            if not r.is_tombstone:
                kept.append(r)
                continue
            # Safe only when no live snapshot could see the record this
            # tombstone deletes.  A tombstone at seq <= oldest visible point is
            # already effective for every surviving snapshot.  With no
            # snapshots every tombstone is safe.
            if oldest is None or r.seq <= oldest:
                dropped += 1
            else:
                kept.append(r)
        return kept, dropped

    def _split_outputs(
        self,
        records: List[Record],
        target_level: int,
        parent_ids: Tuple[int, ...],
    ) -> List[FileMeta]:
        target = self.manifest.config.target_file_records
        outputs: List[FileMeta] = []
        for start in range(0, len(records), target):
            chunk = records[start:start + target]
            if not chunk:
                continue
            fid = self.manifest.alloc_file_id()
            outputs.append(
                FileMeta(
                    file_id=fid,
                    level=target_level,
                    records=chunk,
                    origin="compaction",
                    parents=parent_ids,
                )
            )
        return outputs

    def execute(self, plan: CompactionPlan, strict_version: bool = False) -> Dict[str, Any]:
        self.validate_plan(plan, strict_version=strict_version)
        self.stats.compactions_started += 1
        files = [
            f
            for f in self.manifest.all_files()
            if f.file_id in set(plan.input_files)
        ]
        files.sort(key=lambda f: (f.level, f.min_key, f.file_id))
        merged, input_count, duplicates = self._merge_inputs(files)
        bottom = plan.target_level == self.manifest.config.levels - 1
        survivors, tombstones_dropped = self._drop_bottom_tombstones(merged, bottom)
        # A compaction can produce zero files when all survivors are dropped.
        outputs = self._split_outputs(
            survivors, plan.target_level, tuple(plan.input_files)
        )
        before_physical = sum(f.physical_bytes for f in files)
        obsolete, reclaimed = self.manifest.replace_files(
            set(plan.input_files),
            [(plan.target_level, f) for f in outputs],
        )
        after_physical = sum(f.physical_bytes for f in outputs)
        reclaimed_bytes = sum(f.physical_bytes for f in reclaimed)
        self.stats.compactions_completed += 1
        self.stats.records_input += input_count
        self.stats.records_output += len(survivors)
        self.stats.duplicates_dropped += duplicates
        self.stats.tombstones_dropped += tombstones_dropped
        self.stats.bytes_written += after_physical
        self.stats.bytes_reclaimed += reclaimed_bytes
        self.stats.files_reclaimed += len(reclaimed)
        self.stats.max_watermark = max(
            self.stats.max_watermark, self.manifest.watermark
        )
        return {
            "plan_id": plan.plan_id,
            "source_level": plan.source_level,
            "target_level": plan.target_level,
            "input_files": list(plan.input_files),
            "output_files": [f.file_id for f in outputs],
            "obsolete_files": [f.file_id for f in obsolete],
            "reclaimed_files": [f.file_id for f in reclaimed],
            "records_input": input_count,
            "records_output": len(survivors),
            "duplicates_dropped": duplicates,
            "tombstones_dropped": tombstones_dropped,
            "bytes_written": after_physical,
            "bytes_reclaimed_now": reclaimed_bytes,
            "physical_before": before_physical,
            "physical_after": after_physical,
            "watermark": self.manifest.watermark,
            "bottom": bottom,
            "reason": plan.reason,
        }

    # ------------------------------------------------------------------- reads
    def read(self, key: str, snap_id: Optional[int] = None) -> Any:
        self.stats.point_lookups += 1
        if snap_id is None:
            visible_seq = self.manifest.seq
            level_files = self.manifest.levels
            orphan_files: List[FileMeta] = []
        else:
            snap = self.manifest.snapshots.get(snap_id)
            if snap is None:
                raise KeyError("unknown or released snapshot %r" % snap_id)
            visible_seq = snap.visible_seq
            pinned = set(snap.pinned_files)
            level_files = [
                [f for f in lv if f.file_id in pinned]
                for lv in self.manifest.levels
            ]
            orphan_files = [
                f for f in self.manifest.orphan_files.values()
                if f.file_id in pinned
            ]
        # L0 newest flush first; older versions live in older L0 files and then
        # in the deeper, range-disjoint levels.  The memtable holds the newest
        # unflushed writes and must participate in every current-state read;
        # snapshot reads always flush first, so its seq check excludes it there.
        l0 = sorted(level_files[0], key=lambda f: -f.file_id)
        candidate: Optional[Record] = None
        scanned = 0
        for r in self._memtable:
            if r.key == key and r.seq <= visible_seq:
                scanned += 1
                if candidate is None or newer(r, candidate):
                    candidate = r
        for f in l0:
            scanned += 1
            hit = self._lookup_file(f, key, visible_seq)
            if hit is not None and (candidate is None or newer(hit, candidate)):
                candidate = hit
        for level in range(1, len(level_files)):
            for f in level_files[level]:
                if not f.overlaps(key, key):
                    continue
                scanned += 1
                hit = self._lookup_file(f, key, visible_seq)
                if hit is not None and (candidate is None or newer(hit, candidate)):
                    candidate = hit
        for f in orphan_files:
            scanned += 1
            hit = self._lookup_file(f, key, visible_seq)
            if hit is not None and (candidate is None or newer(hit, candidate)):
                candidate = hit
        self.stats.files_scanned += scanned
        if candidate is None or candidate.is_tombstone:
            return None
        return candidate.value

    @staticmethod
    def _lookup_file(f: FileMeta, key: str, visible_seq: int) -> Optional[Record]:
        best: Optional[Record] = None
        for r in f.records:
            if r.key == key and r.seq <= visible_seq:
                if best is None or newer(r, best):
                    best = r
        return best

    # ------------------------------------------------------------------ status
    def space_amplification(self) -> float:
        live = self.manifest.live_logical_bytes()
        if live == 0:
            return 1.0
        return self.manifest.physical_bytes() / float(live)

    def write_amplification(self) -> float:
        # Total bytes physically written (flush + compaction rewrites) divided
        # by the logical bytes originally ingested.
        total = self.stats.bytes_ingested
        if total == 0:
            return 1.0
        return self.stats.bytes_written / float(total)

    def level_summary(self) -> List[Dict[str, Any]]:
        out = []
        for i, lv in enumerate(self.manifest.levels):
            out.append(
                {
                    "level": i,
                    "files": len(lv),
                    "cap": self.manifest.config.file_caps[i],
                    "logical_bytes": sum(f.logical_bytes for f in lv),
                    "physical_bytes": sum(f.physical_bytes for f in lv),
                    "key_ranges": [[f.min_key, f.max_key] for f in lv],
                }
            )
        return out
