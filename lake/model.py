"""Core data model: records, files, engine configuration.

The model is deliberately dependency free so that manifest state can be
serialised to JSON and replayed byte for byte.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Tuple

# Record overhead: key length + value length + seq/ts/index bookkeeping bytes.
_RECORD_META_BYTES = 16
# Every data file carries a fixed footer / metadata cost.  Keeping this > 0 is
# what makes *small* files expensive in the space-amplification metric.
_FILE_FOOTER_BYTES = 16
TOMBSTONE = None


@dataclass(frozen=True)
class Record:
    """A single version of a key.

    ``seq`` is the globally monotonic batch number assigned by the engine.
    ``ts`` is the application timestamp; ties and cross-batch ordering are
    always broken by ``seq`` so two writers can never share an identity.
    ``write_index`` disambiguates records produced inside one batch.
    """

    key: str
    seq: int
    ts: int
    write_index: int
    value: Any = TOMBSTONE

    @property
    def is_tombstone(self) -> bool:
        return self.value is TOMBSTONE

    def size_bytes(self) -> int:
        # JSON-ish estimate: the string key plus a serialised value plus header.
        value_size = 0 if self.value is None else len(repr(self.value))
        return len(self.key.encode("utf-8")) + value_size + _RECORD_META_BYTES

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "seq": self.seq,
            "ts": self.ts,
            "write_index": self.write_index,
            "value": self.value,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Record":
        return Record(
            key=d["key"],
            seq=d["seq"],
            ts=d["ts"],
            write_index=d["write_index"],
            value=d["value"],
        )


def newer(a: Record, b: Record) -> bool:
    """Total ordering used for merge淘汰: batch seq first, then ts, then index."""
    return (a.seq, a.ts, a.write_index) > (b.seq, b.ts, b.write_index)


@dataclass
class FileMeta:
    """Immutable data file: sorted records, key range and sequence range."""

    file_id: int
    level: int
    records: List[Record]
    min_key: str = ""
    max_key: str = ""
    min_seq: int = 0
    max_seq: int = 0
    logical_bytes: int = 0
    # Files only become reclaimable when no snapshot pins them any more.
    pin_count: int = 0
    # Set once the file is replaced by a compaction; it may still be pinned.
    obsolete: bool = False
    # Optional provenance for decision replay.
    origin: str = "flush"
    parents: Tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.records:
            raise ValueError("empty data files are not allowed")
        keys = [r.key for r in self.records]
        seqs = [r.seq for r in self.records]
        self.min_key = min(keys)
        self.max_key = max(keys)
        self.min_seq = min(seqs)
        self.max_seq = max(seqs)
        if self.logical_bytes == 0:
            self.logical_bytes = sum(r.size_bytes() for r in self.records)

    @property
    def physical_bytes(self) -> int:
        return self.logical_bytes + _FILE_FOOTER_BYTES

    def overlaps(self, min_key: str, max_key: str) -> bool:
        return not (self.max_key < min_key or self.min_key > max_key)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["parents"] = list(self.parents)
        d["records"] = [r.to_dict() for r in self.records]
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "FileMeta":
        return FileMeta(
            file_id=d["file_id"],
            level=d["level"],
            records=[Record.from_dict(r) for r in d["records"]],
            min_key=d["min_key"],
            max_key=d["max_key"],
            min_seq=d["min_seq"],
            max_seq=d["max_seq"],
            logical_bytes=d["logical_bytes"],
            pin_count=d["pin_count"],
            obsolete=d["obsolete"],
            origin=d.get("origin", "flush"),
            parents=tuple(d.get("parents", ())),
        )


@dataclass
class Config:
    """Layered store configuration.

    ``file_caps[i]`` is the number of files tolerated at level i before it is
    considered over capacity.  ``memtable_records`` controls flush granularity,
    ``target_file_records`` splits compaction outputs to keep files compact.
    """

    levels: int = 4
    file_caps: Tuple[int, ...] = (4, 4, 8, 16)
    memtable_records: int = 4
    target_file_records: int = 12
    max_concurrent_compactions: int = 2
    # Hard guard against pathological write amplification.
    max_compaction_inputs: int = 32

    def __post_init__(self) -> None:
        if self.levels < 2:
            raise ValueError("at least two levels are required")
        if len(self.file_caps) != self.levels:
            raise ValueError("file_caps length must match level count")
        if min(self.file_caps) <= 0 or self.memtable_records <= 0:
            raise ValueError("capacities must be positive")
        if self.max_concurrent_compactions < 1:
            raise ValueError("at least one compaction slot is required")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "levels": self.levels,
            "file_caps": list(self.file_caps),
            "memtable_records": self.memtable_records,
            "target_file_records": self.target_file_records,
            "max_concurrent_compactions": self.max_concurrent_compactions,
            "max_compaction_inputs": self.max_compaction_inputs,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Config":
        return Config(
            levels=d["levels"],
            file_caps=tuple(d["file_caps"]),
            memtable_records=d.get("memtable_records", 4),
            target_file_records=d.get("target_file_records", 12),
            max_concurrent_compactions=d.get("max_concurrent_compactions", 2),
            max_compaction_inputs=d.get("max_compaction_inputs", 32),
        )
