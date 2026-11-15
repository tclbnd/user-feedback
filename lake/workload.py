"""Deterministic mixed read/write/delete/overwrite/snapshot workloads."""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class WorkloadSpec:
    n_ops: int = 240
    key_space: int = 24
    seed: int = 7
    # Long-lived snapshots are opened periodically and released a few ops later
    # so compaction must preserve versions across their lifetime.
    snapshot_every: int = 37
    snapshot_hold: int = 29
    read_every: int = 5
    put_ratio: float = 0.62
    delete_ratio: float = 0.20
    # Remaining weight is overwrite of an existing key.
    max_long_snaps: int = 2


def generate_ops(spec: WorkloadSpec) -> List[Dict[str, Any]]:
    rng = random.Random(spec.seed)
    ops: List[Dict[str, Any]] = []
    live_keys: List[str] = []
    active: List[Tuple[int, int]] = []  # (release_at, snap_id)
    snap_seq = 0
    for i in range(spec.n_ops):
        # Release due snapshots first so their pins disappear deterministically.
        for release_at, sid in list(active):
            if release_at == i:
                ops.append({"op": "release", "snap": sid})
                active.remove((release_at, sid))
        roll = rng.random()
        if i % spec.read_every == 0 and i > 0:
            key = "k%03d" % rng.randrange(spec.key_space)
            # Roughly half the reads target a currently open snapshot.
            if active and rng.random() < 0.5:
                sid = active[rng.randrange(len(active))][1]
                ops.append({"op": "read", "k": key, "snap": sid})
            else:
                ops.append({"op": "read", "k": key})
        elif roll < spec.put_ratio:
            key = "k%03d" % rng.randrange(spec.key_space)
            ops.append({"op": "put", "k": key, "v": rng.randint(1, 1_000_000)})
            if key not in live_keys:
                live_keys.append(key)
        elif roll < spec.put_ratio + spec.delete_ratio:
            key = "k%03d" % rng.randrange(spec.key_space)
            ops.append({"op": "del", "k": key})
        else:
            if live_keys:
                key = live_keys[rng.randrange(len(live_keys))]
            else:
                key = "k%03d" % rng.randrange(spec.key_space)
            ops.append({"op": "put", "k": key, "v": rng.randint(1, 1_000_000)})
            if key not in live_keys:
                live_keys.append(key)
        if (
            i > 0
            and i % spec.snapshot_every == 0
            and len(active) < spec.max_long_snaps
        ):
            snap_seq += 1
            sid = snap_seq
            ops.append({"op": "snap", "snap": sid})
            active.append((i + spec.snapshot_hold, sid))
    # Release everything still open at the end so the final state can converge.
    for _, sid in sorted(active):
        ops.append({"op": "release", "snap": sid})
    return ops


class ReferenceDB:
    """Serial ground truth: full version history plus snapshot visibility."""

    def __init__(self) -> None:
        # key -> list of (seq, ts, value) newest first
        self.history: Dict[str, List[Tuple[int, int, Any]]] = {}
        self.seq = 0
        self.snapshots: Dict[int, int] = {}
        self.reads: List[Any] = []
        self.read_observations: List[Dict[str, Any]] = []

    def apply(self, op: Dict[str, Any]) -> None:
        kind = op["op"]
        if kind in ("put", "del"):
            self.seq += 1
            value = op.get("v") if kind == "put" else None
            self.history.setdefault(op["k"], []).append(
                (self.seq, self.seq, value)
            )
        elif kind == "snap":
            self.snapshots[op["snap"]] = self.seq
        elif kind == "release":
            self.snapshots.pop(op["snap"], None)
        elif kind == "read":
            snap_id = op.get("snap")
            if snap_id is None:
                visible = self.seq
            else:
                visible = self.snapshots[snap_id]
            value = None
            # Versions are appended newest-last; scan newest -> oldest and skip
            # versions created after this snapshot/current seq until a visible
            # version is found.
            for s, _ts, v in reversed(self.history.get(op["k"], [])):
                if s <= visible:
                    value = v
                    break
            self.reads.append(value)
            self.read_observations.append(
                {"k": op["k"], "snap": snap_id, "visible_seq": visible,
                 "value": value}
            )
        else:
            raise ValueError("unknown op %r" % kind)

    def final_kv(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, versions in self.history.items():
            # appended newest last; take the final version
            _seq, _ts, value = versions[-1]
            if value is not None:
                out[key] = value
        return dict(sorted(out.items()))
