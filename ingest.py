"""Capture log ingestion.

The JSONL log records, per recording generation (epoch):

* ``open``  - clock origin, timescale and init section of every track;
* ``fragment`` - one-track fMP4 fragment, identified by ``(epoch, track, seq)``;
* ``seal``  - the final expected sequence number per track.

Fragments of a track may arrive out of order within a bounded window or be
redelivered.  A redelivery whose checksum matches the stored fragment is a
no-op; a complete, verified fragment with the same id but different content
raises :class:`ConflictError`.  A truncated delivery (checksum mismatch) stays
in an incomplete state and may be replaced by a later complete redelivery.
Only after a generation is sealed can missing numbers be treated as permanent.
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# A fragment may show up while up to this many earlier numbers are pending.
MAX_WINDOW = 4


class IngestError(ValueError):
    """Base class for malformed or inconsistent capture logs."""


class ConflictError(IngestError):
    """Same (epoch, track, seq) delivered twice with different content."""


class WindowError(IngestError):
    """A fragment arrived more than ``MAX_WINDOW`` positions ahead."""


@dataclass
class TrackSpec:
    init_path: Path
    init_bytes: bytes
    timescale: int
    clock_origin: int


@dataclass
class StoredFragment:
    seq: int
    path: Path
    sha256: str
    summary: str
    complete: bool
    data: Optional[bytes] = None


@dataclass
class EpochState:
    epoch: str
    tracks: Dict[str, TrackSpec]
    fragments: Dict[str, Dict[int, StoredFragment]] = field(default_factory=dict)
    sealed: bool = False
    last: Dict[str, int] = field(default_factory=dict)

    def stored(self, track: str, seq: int) -> Optional[StoredFragment]:
        return self.fragments.get(track, {}).get(seq)


@dataclass
class CaptureSession:
    log_path: Path
    epochs: List[EpochState] = field(default_factory=list)
    order: List[str] = field(default_factory=list)

    def epoch(self, name: str) -> EpochState:
        for state in self.epochs:
            if state.epoch == name:
                return state
        raise IngestError(f"unknown epoch: {name}")

    def is_known(self, name: str) -> bool:
        return any(state.epoch == name for state in self.epochs)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_capture(log: Path) -> CaptureSession:
    session = CaptureSession(log_path=log)
    for line_number, line in enumerate(log.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise IngestError(f"line {line_number}: invalid JSON: {exc}") from exc
        kind = event.get("type")
        handler = {
            "open": _handle_open,
            "fragment": _handle_fragment,
            "seal": _handle_seal,
        }.get(kind)
        if handler is None:
            raise IngestError(f"line {line_number}: unexpected event: {kind}")
        handler(session, log.parent, event, line_number)

    if not session.epochs or not all(state.sealed for state in session.epochs):
        raise IngestError("a complete, sealed capture is required")
    return session


def _handle_open(session: CaptureSession, base: Path, event: dict, line: int) -> None:
    name = event["epoch"]
    if session.is_known(name):
        raise IngestError(f"line {line}: epoch {name} opened twice")
    tracks: Dict[str, TrackSpec] = {}
    for track, spec in event["tracks"].items():
        init_path = base / spec["init"]
        tracks[track] = TrackSpec(
            init_path=init_path,
            init_bytes=init_path.read_bytes(),
            timescale=int(spec["timescale"]),
            clock_origin=int(spec.get("clock_origin", 0)),
        )
    session.epochs.append(EpochState(epoch=name, tracks=tracks))
    session.order.append(name)


def _handle_fragment(session: CaptureSession, base: Path, event: dict,
                     line: int) -> None:
    name = event["epoch"]
    if not session.is_known(name):
        raise IngestError(f"line {line}: fragment for unknown epoch {name}")
    epoch = session.epoch(name)
    if epoch.sealed:
        raise IngestError(f"line {line}: fragment after seal for epoch {name}")
    track_name = event["track"]
    if track_name not in epoch.tracks:
        raise IngestError(f"line {line}: unknown track {track_name}")
    seq = int(event["seq"])
    if seq < 1:
        raise IngestError(f"line {line}: fragment sequence numbers start at 1")

    stored = epoch.stored(track_name, seq)
    media_path = base / event["path"]
    raw = media_path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    expected = event["sha256"]
    summary = str(event.get("summary", ""))
    complete = actual == expected

    if stored is not None:
        if stored.complete:
            if complete and actual == stored.sha256:
                return  # idempotent redelivery
            raise ConflictError(
                f"line {line}: conflicting {track_name} fragment {seq} in "
                f"{name}: stored sha256 {stored.sha256[:12]} vs {actual[:12]}")
        # Earlier delivery was truncated; a complete redelivery replaces it.
        if not complete:
            return  # still truncated; keep waiting
    else:
        queued = sorted(epoch.fragments.get(track_name, {}))
        if not queued:
            # Nothing queued yet: bound how far the first arrival may lead the
            # start of the sequence.
            if seq > 1 + MAX_WINDOW:
                raise WindowError(
                    f"line {line}: {track_name} fragment {seq} arrives more "
                    f"than {MAX_WINDOW} numbers ahead of the first fragment")
        else:
            # Limit reordering depth relative to what is already queued.
            # A permanent gap (a number that never arrives) does not count as
            # out-of-order delivery, so only the span past the highest queued
            # number is bounded.
            highest = queued[-1]
            if seq > highest + MAX_WINDOW:
                raise WindowError(
                    f"line {line}: {track_name} fragment {seq} arrives more "
                    f"than {MAX_WINDOW} numbers past queued {highest}")

    bucket = epoch.fragments.setdefault(track_name, {})
    bucket[seq] = StoredFragment(
        seq=seq, path=media_path, sha256=expected, summary=summary,
        complete=complete, data=raw if complete else None)


def _handle_seal(session: CaptureSession, base: Path, event: dict,
                 line: int) -> None:
    name = event["epoch"]
    if not session.is_known(name):
        raise IngestError(f"line {line}: seal for unknown epoch {name}")
    epoch = session.epoch(name)
    if epoch.sealed:
        raise IngestError(f"line {line}: epoch {name} sealed twice")
    last = {track: int(number) for track, number in event["last"].items()}
    if set(last) != set(epoch.tracks):
        raise IngestError(f"line {line}: seal must name every track")
    for track, number in last.items():
        if number < 0:
            raise IngestError(f"line {line}: negative last sequence")
    # Anything still truncated at seal time can never become complete.
    for track in last:
        for seq, stored in epoch.fragments.get(track, {}).items():
            if not stored.complete:
                raise IngestError(
                    f"line {line}: truncated {track} fragment {seq} in {name} "
                    "was never redelivered")
    epoch.sealed = True
    epoch.last = last


def missing_numbers(epoch: EpochState, track: str) -> List[int]:
    """Permanent gaps for a sealed track (seqs 1..last without complete data)."""
    if not epoch.sealed:
        raise IngestError("missing numbers are only defined after seal")
    have = {seq for seq, item in epoch.fragments.get(track, {}).items()
            if item.complete}
    return [seq for seq in range(1, epoch.last[track] + 1) if seq not in have]


def ordered_fragments(epoch: EpochState, track: str) -> List[StoredFragment]:
    """Complete fragments in sequence order (missing seqs skipped)."""
    return [epoch.fragments[track][seq]
            for seq in sorted(epoch.fragments.get(track, {}))
            if epoch.fragments[track][seq].complete]
