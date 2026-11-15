"""Durable-ish in-memory manifest with JSON snapshot/restore.

The manifest owns:
  * level -> ordered list of live data files,
  * snapshots and their pinned file ids,
  * the garbage-collection watermark (highest sequence whose older versions
    are eligible for reclamation), which is asserted monotonic,
  * the id allocator and monotonically increasing version counter.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .model import Config, FileMeta, Record


class WatermarkRegression(RuntimeError):
    """Raised if any code path attempts to move the watermark backwards."""


@dataclass
class Snapshot:
    snap_id: int
    visible_seq: int
    pinned_files: Tuple[int, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snap_id": self.snap_id,
            "visible_seq": self.visible_seq,
            "pinned_files": list(self.pinned_files),
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Snapshot":
        return Snapshot(
            snap_id=d["snap_id"],
            visible_seq=d["visible_seq"],
            pinned_files=tuple(d["pinned_files"]),
        )


@dataclass
class Manifest:
    config: Config
    levels: List[List[FileMeta]] = field(default_factory=list)
    seq: int = 0
    next_file_id: int = 1
    next_snap_id: int = 0
    # Monotonic version counter bumped on every structural change.
    state_version: int = 0
    # GC watermark: versions with seq <= watermark may be physically reclaimed
    # once no snapshot pins their containing file.  It only moves forward.
    watermark: int = 0
    snapshots: Dict[int, Snapshot] = field(default_factory=dict)
    # Obsolete files retained only because a snapshot still pins them.
    orphan_files: Dict[int, FileMeta] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.levels:
            self.levels = [[] for _ in range(self.config.levels)]

    # ------------------------------------------------------------------ basic
    def all_files(self) -> List[FileMeta]:
        out: List[FileMeta] = []
        for lv in self.levels:
            out.extend(lv)
        return out

    def file_by_id(self, file_id: int) -> Optional[FileMeta]:
        for f in self.all_files():
            if f.file_id == file_id:
                return f
        return self.orphan_files.get(file_id)

    def alloc_file_id(self) -> int:
        fid = self.next_file_id
        self.next_file_id += 1
        return fid

    def bump(self) -> None:
        self.state_version += 1

    def current_max_seq(self) -> int:
        mx = self.seq
        for f in self.all_files():
            mx = max(mx, f.max_seq)
        return mx

    # --------------------------------------------------------------- snapshots
    def create_snapshot(self, snap_id: Optional[int] = None) -> Snapshot:
        if snap_id is None:
            snap_id = self.next_snap_id
        if snap_id in self.snapshots:
            raise ValueError("snapshot id %r already exists" % snap_id)
        self.next_snap_id = max(self.next_snap_id, snap_id + 1)
        pinned = tuple(sorted(f.file_id for f in self.all_files()))
        snap = Snapshot(snap_id=snap_id, visible_seq=self.seq, pinned_files=pinned)
        self.snapshots[snap_id] = snap
        for fid in pinned:
            f = self.file_by_id(fid)
            assert f is not None
            f.pin_count += 1
        self.bump()
        return snap

    def release_snapshot(self, snap_id: int) -> bool:
        snap = self.snapshots.pop(snap_id, None)
        if snap is None:
            return False
        for fid in snap.pinned_files:
            f = self.file_by_id(fid)
            # File may already have been physically deleted by a prior GC.
            if f is not None:
                f.pin_count -= 1
                if f.pin_count < 0:
                    raise AssertionError("pin count went negative")
        self.bump()
        # Releasing a snapshot can both raise the watermark and make orphaned
        # files reclaimable in the same step.
        self.advance_watermark()
        self.gc_orphans()
        return True

    def oldest_snapshot_seq(self) -> Optional[int]:
        if not self.snapshots:
            return None
        return min(s.visible_seq for s in self.snapshots.values())

    # -------------------------------------------------------------- watermark
    def advance_watermark(self) -> int:
        """Recompute the safe GC watermark and enforce 只进不退."""
        oldest = self.oldest_snapshot_seq()
        if oldest is None:
            candidate = self.current_max_seq()
        else:
            # Keep every version that could be visible to the oldest snapshot.
            # Sequence numbers start at 1, so the GC watermark floors at 0.
            # While a long-lived snapshot exists, newer writes cannot raise the
            # safe point, and it must never drag an already-advanced watermark
            # backwards either: 水位只进不退.
            candidate = max(self.watermark, max(0, oldest - 1))
        if candidate < self.watermark:
            raise WatermarkRegression(
                "watermark regression %d -> %d" % (self.watermark, candidate)
            )
        self.watermark = candidate
        return self.watermark

    # -------------------------------------------------------------- structure
    def add_file(self, level: int, f: FileMeta) -> None:
        self.levels[level].append(f)
        self.levels[level].sort(key=lambda x: (x.min_key, x.file_id))
        self.bump()

    def replace_files(
        self,
        input_ids: Set[int],
        outputs: List[Tuple[int, FileMeta]],
    ) -> Tuple[List[FileMeta], List[FileMeta]]:
        """Atomically swap compacted inputs for outputs.

        Returns ``(obsolete, reclaimed)``.  Obsolete files stay around while a
        snapshot pins them; unpinned files are reclaimed immediately.
        """
        input_set = set(input_ids)
        obsolete: List[FileMeta] = []
        reclaimed: List[FileMeta] = []
        new_levels: List[List[FileMeta]] = [[] for _ in self.levels]
        seen = set()
        for li, level_files in enumerate(self.levels):
            for f in level_files:
                if f.file_id in input_set:
                    if f.file_id in seen:
                        continue
                    seen.add(f.file_id)
                    f.obsolete = True
                    obsolete.append(f)
                    if f.pin_count == 0:
                        reclaimed.append(f)
                    else:
                        self.orphan_files[f.file_id] = f
                else:
                    new_levels[li].append(f)
        if seen != input_set:
            missing = input_set - seen
            raise KeyError("compaction inputs not found in manifest: %r" % missing)
        for target_level, out in outputs:
            new_levels[target_level].append(out)
        for li in range(len(self.levels)):
            new_levels[li].sort(key=lambda x: (x.min_key, x.file_id))
        self.levels = new_levels
        self.bump()
        self.advance_watermark()
        self.gc_orphans()
        return obsolete, reclaimed

    def gc_orphans(self) -> List[FileMeta]:
        reclaimed = []
        for fid in list(self.orphan_files):
            f = self.orphan_files[fid]
            if f.pin_count == 0:
                del self.orphan_files[fid]
                reclaimed.append(f)
                self.bump()
        return reclaimed

    # ------------------------------------------------------------- statistics
    def live_logical_bytes(self) -> int:
        """Latest visible version per key (tombstones contribute zero)."""
        latest: Dict[str, Record] = {}
        for level_files in self.levels:
            for f in level_files:
                for r in f.records:
                    cur = latest.get(r.key)
                    if cur is None or (r.seq, r.ts, r.write_index) > (
                        cur.seq,
                        cur.ts,
                        cur.write_index,
                    ):
                        latest[r.key] = r
        total = 0
        for r in latest.values():
            if not r.is_tombstone:
                total += r.size_bytes()
        return total

    def physical_bytes(self) -> int:
        total = sum(f.physical_bytes for f in self.all_files())
        total += sum(f.physical_bytes for f in self.orphan_files.values())
        return total

    def live_key_values(self) -> Dict[str, Any]:
        latest: Dict[str, Record] = {}
        for level_files in self.levels:
            for f in level_files:
                for r in f.records:
                    cur = latest.get(r.key)
                    if cur is None or (r.seq, r.ts, r.write_index) > (
                        cur.seq,
                        cur.ts,
                        cur.write_index,
                    ):
                        latest[r.key] = r
        return {
            k: r.value
            for k, r in sorted(latest.items())
            if not r.is_tombstone
        }

    # ----------------------------------------------------------- serialisation
    def to_dict(self) -> Dict[str, Any]:
        return {
            "config": self.config.to_dict(),
            "levels": [[f.to_dict() for f in lv] for lv in self.levels],
            "seq": self.seq,
            "next_file_id": self.next_file_id,
            "next_snap_id": self.next_snap_id,
            "state_version": self.state_version,
            "watermark": self.watermark,
            "snapshots": {str(k): s.to_dict() for k, s in self.snapshots.items()},
            "orphan_files": {str(k): f.to_dict() for k, f in self.orphan_files.items()},
        }

    def digest(self) -> str:
        blob = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()

    def save_json(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, sort_keys=True, indent=1)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Manifest":
        config = Config.from_dict(d["config"])
        m = Manifest(config=config)
        m.levels = [
            [FileMeta.from_dict(fd) for fd in lv] for lv in d["levels"]
        ]
        m.seq = d["seq"]
        m.next_file_id = d["next_file_id"]
        m.next_snap_id = d["next_snap_id"]
        m.state_version = d["state_version"]
        m.watermark = d["watermark"]
        m.snapshots = {
            int(k): Snapshot.from_dict(v) for k, v in d["snapshots"].items()
        }
        m.orphan_files = {
            int(k): FileMeta.from_dict(v) for k, v in d["orphan_files"].items()
        }
        return m
