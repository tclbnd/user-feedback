"""Read a capture log and turn it into a deterministic recording model.

The log is JSON Lines.  Three event types are understood (see README):

``open``
    Starts a recording generation (``epoch``).  It defines one entry per track
    with the init segment, media timescale and the track-tick instant
    (``clock_origin``) that both tracks share.

``fragment``
    Delivers one fragment.  ``(epoch, track, seq)`` identifies the slot; the
    file is only accepted when it exists, has the declared size (if given) and
    its SHA-256 digest matches.  Arrivals may be out of order and the same slot
    may be delivered more than once.  A truncated/invalid delivery is ignored
    and may be followed by a complete one.  Two *complete* deliveries for the
    same slot with different content are a conflict and abort the capture.

``seal``
    Declares the last expected sequence number for every track of a generation.
    Only after this event may holes in a track be treated as permanently
    missing.

All files are resolved relative to the directory containing the log.
"""

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional

HEX_DIGITS = set("0123456789abcdefABCDEF")


class CaptureError(ValueError):
    """Raised when the capture log is inconsistent or cannot be published."""


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_digest(value, where: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or not set(value) <= HEX_DIGITS:
        raise CaptureError(f"{where}: invalid sha256 value")
    return value.lower()


def _as_int(value, where: str, minimum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CaptureError(f"{where}: expected an integer")
    if minimum is not None and value < minimum:
        raise CaptureError(f"{where}: value is smaller than {minimum}")
    return value


def _track_model(path: Path, data: dict, where: str) -> dict:
    init_name = data.get("init")
    if not isinstance(init_name, str) or not init_name:
        raise CaptureError(f"{where}: init segment path is required")
    init = path / init_name
    if not init.is_file():
        raise CaptureError(f"{where}: init segment {init} is not readable")
    return {
        "init": init,
        "init_sha256": _digest(init),
        "timescale": _as_int(data.get("timescale"), f"{where}.timescale", 1),
        "clock_origin": _as_int(data.get("clock_origin", 0),
                                f"{where}.clock_origin"),
        "candidates": {},
        "rejected": [],
        "last": None,
        "sealed": False,
    }


def read_capture(log: Path, require_sealed: bool = True) -> dict:
    log = Path(log)
    root = log.parent
    epochs: List[dict] = []
    by_id: Dict[str, dict] = {}

    for number, line in enumerate(
            log.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise CaptureError(f"{log}:{number}: invalid JSON: {error}") from error
        if not isinstance(event, dict) or "type" not in event:
            raise CaptureError(f"{log}:{number}: event is not an object with a type")
        kind = event["type"]
        where = f"{log}:{number}"

        if kind == "open":
            epoch_id = event.get("epoch")
            if not isinstance(epoch_id, str) or not epoch_id:
                raise CaptureError(f"{where}: open event needs an epoch")
            if epoch_id in by_id:
                raise CaptureError(f"{where}: epoch {epoch_id!r} opened twice")
            tracks = event.get("tracks")
            if not isinstance(tracks, dict) or not tracks:
                raise CaptureError(f"{where}: open event needs tracks")
            model = {
                "epoch": epoch_id,
                "order": len(epochs),
                "sealed": False,
                "last": None,
                "tracks": {},
            }
            for name, data in tracks.items():
                if not isinstance(name, str) or not isinstance(data, dict):
                    raise CaptureError(f"{where}: invalid track definition")
                track_model = _track_model(root, data, f"{where}.{name}")
                track_model["epoch"] = epoch_id
                model["tracks"][name] = track_model
            epochs.append(model)
            by_id[epoch_id] = model

        elif kind == "fragment":
            epoch_id = event.get("epoch")
            if not isinstance(epoch_id, str) or epoch_id not in by_id:
                raise CaptureError(f"{where}: fragment for unknown epoch")
            epoch = by_id[epoch_id]
            name = event.get("track")
            if not isinstance(name, str):
                raise CaptureError(f"{where}: fragment track name is required")
            if name not in epoch["tracks"]:
                raise CaptureError(f"{where}: fragment for unknown track")
            track = epoch["tracks"][name]
            seq = _as_int(event.get("seq"), f"{where}.seq", 1)
            where += f" ({epoch_id}/{name}/{seq})"
            declared = _validate_digest(event.get("sha256"), where)

            accepted = track["candidates"].get(seq)
            if accepted is not None and accepted["sha256"] == declared:
                # A byte-identical redelivery.  Nothing to do; it must not
                # depend on the arrival order or on how often it was seen.
                continue

            media_name = event.get("path")
            if not isinstance(media_name, str) or not media_name:
                raise CaptureError(f"{where}: fragment path is required")
            media = root / media_name
            if not media.is_file():
                track["rejected"].append({
                    "seq": seq, "sha256": declared, "path": str(media),
                    "reason": "missing",
                })
                continue
            declared_size = event.get("size")
            if declared_size is not None:
                if not isinstance(declared_size, int) or declared_size < 0:
                    raise CaptureError(f"{where}: invalid size")
                if media.stat().st_size != declared_size:
                    track["rejected"].append({
                        "seq": seq, "sha256": declared, "path": str(media),
                        "reason": "size",
                    })
                    continue
            actual = _digest(media)
            if actual != declared:
                # Truncated or not yet fully written.  A complete delivery may
                # still arrive later, so this is not a conflict.
                track["rejected"].append({
                    "seq": seq, "sha256": declared, "path": str(media),
                    "reason": "digest",
                })
                continue

            if accepted is not None:
                raise CaptureError(
                    "fragment conflict at %s: seq %d was delivered completely "
                    "with sha256 %s and again with %s"
                    % (f"{epoch_id}/{name}", seq, accepted["sha256"], actual)
                )
            track["candidates"][seq] = {
                "seq": seq, "path": media, "sha256": actual, "size":
                media.stat().st_size,
            }

        elif kind == "seal":
            epoch_id = event.get("epoch")
            if not isinstance(epoch_id, str) or epoch_id not in by_id:
                raise CaptureError(f"{where}: seal for unknown epoch")
            epoch = by_id[epoch_id]
            if epoch["sealed"]:
                raise CaptureError(f"{where}: epoch {epoch_id!r} sealed twice")
            last = event.get("last")
            if not isinstance(last, dict):
                raise CaptureError(f"{where}: seal event needs a last mapping")
            for name in epoch["tracks"]:
                if name not in last:
                    raise CaptureError(
                        f"{where}: seal is missing track {name!r}"
                    )
            for name in last:
                if name not in epoch["tracks"]:
                    raise CaptureError(
                        f"{where}: seal has unknown track {name!r}"
                    )
            for name, value in last.items():
                value = _as_int(value, f"{where}.last.{name}", 0)
                for seq in epoch["tracks"][name]["candidates"]:
                    if seq > value:
                        raise CaptureError(
                            f"{where}: fragment {name}/{seq} is beyond the "
                            f"sealed last value {value}"
                        )
                epoch["tracks"][name]["last"] = value
            epoch["sealed"] = True
            epoch["last"] = {name: epoch["tracks"][name]["last"]
                             for name in epoch["tracks"]}

        else:
            raise CaptureError(f"{where}: unsupported event type {kind!r}")

    if not epochs:
        raise CaptureError("capture log is empty")
    if require_sealed:
        for epoch in epochs:
            if not epoch["sealed"]:
                raise CaptureError(
                    "a sealed capture is required (epoch %r was not sealed)"
                    % epoch["epoch"]
                )

    for epoch in epochs:
        for name, track in epoch["tracks"].items():
            last = track["last"]
            sealed = epoch["sealed"]
            if last is None:
                last = max(track["candidates"], default=0)
            track["fragments"] = [
                track["candidates"][seq]["path"]
                for seq in sorted(track["candidates"]) if seq <= last
            ]
            # Before sealing, a hole may still be filled and is therefore not
            # reported as permanently missing.
            track["missing"] = ([
                seq for seq in range(1, last + 1)
                if seq not in track["candidates"]
            ] if sealed else [])
            track["last"] = last

    session = {
        "epochs": epochs,
        "epoch": epochs[0]["epoch"],
        "tracks": epochs[0]["tracks"],
        "sealed": all(epoch["sealed"] for epoch in epochs),
    }
    return session


__all__ = ["CaptureError", "read_capture"]
