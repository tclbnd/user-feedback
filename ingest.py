#!/usr/bin/env python3
"""Versioned, event-at-a-time NDJSON import into columnar tables.

The public model is intentionally small:

* one ``events`` row per currently accepted event id (highest valid revision),
* zero or more ordered ``readings`` rows for that event,
* a cumulative, idempotent ``rejects`` table.

Every publication is an immutable directory under ``ROOT/versions/vN``.  The
three Parquet files and the private publication state are written into a
staging directory first and the directory is then renamed into place, so a
version is never visible with only part of its state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

# ---------------------------------------------------------------------------
# Public column schemas.  Extra columns are allowed by the contract, but the
# required ones must keep these names and meanings.
# ---------------------------------------------------------------------------

EVENTS_SCHEMA = pa.schema(
    [
        pa.field("id", pa.string()),
        pa.field("rev", pa.int64()),
        pa.field("device", pa.string()),
        pa.field("array_state", pa.string()),
    ]
)

READINGS_SCHEMA = pa.schema(
    [
        pa.field("id", pa.string()),
        pa.field("rev", pa.int64()),
        pa.field("ordinal", pa.int64()),
        pa.field("channel", pa.string()),
        pa.field("value", pa.int64()),
    ]
)

REJECTS_SCHEMA = pa.schema(
    [
        pa.field("id", pa.string()),
        pa.field("rev", pa.int64()),
        pa.field("source", pa.string()),
        pa.field("line", pa.int64()),
        pa.field("path", pa.string()),
        pa.field("reason", pa.string()),
    ]
)

INT64_MIN = -(2 ** 63)
INT64_MAX = 2 ** 63 - 1

_VERSION_DIR_RE = re.compile(r"^v([0-9]+)$")

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _is_int(value: Any) -> bool:
    """True for JSON integers, excluding booleans (which subclass int)."""

    return isinstance(value, int) and not isinstance(value, bool)


def _json_pointer_part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _path(*parts: Any) -> str:
    """Build a JSON-pointer-like path such as ``/samples/1/v``."""

    escaped = [_json_pointer_part(str(part)) for part in parts]
    return "/" + "/".join(escaped)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _rows_to_table(rows: Iterable[Dict[str, Any]], schema: pa.Schema) -> pa.Table:
    rows = list(rows)
    columns = {name: [row.get(name) for row in rows] for name in schema.names}
    return pa.Table.from_pydict(columns, schema=schema)


def _write_table(rows: Iterable[Dict[str, Any]], schema: pa.Schema, path: Path) -> None:
    pq.write_table(_rows_to_table(rows, schema), path)


def _read_table(path: Path, schema: pa.Schema) -> List[Dict[str, Any]]:
    """Read required columns, tolerating extra columns in newer snapshots."""

    if not path.exists():
        return []
    table = pq.read_table(path)
    num_rows = table.num_rows
    arrays = []
    for field in schema:
        if field.name in table.schema.names:
            column = table.column(field.name)
            if column.type != field.type:
                column = column.cast(field.type)
            arrays.append(column)
        else:
            # A missing required column means every row has no value there.
            arrays.append(
                pa.chunked_array([pa.nulls(num_rows, type=field.type)])
            )
    projected = pa.Table.from_arrays(arrays, schema=schema)
    return projected.to_pylist()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class EventProblem(Exception):
    """The first reason an event cannot become a parent/child row set."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(reason)
        self.path = path
        self.reason = reason


def _require_non_empty_string(value: Any, *, path: str, what: str) -> str:
    if not isinstance(value, str) or value == "":
        raise EventProblem(path, what + " must be a non-empty string")
    return value


def _validate_event(obj: Any) -> Dict[str, Any]:
    """Validate one decoded JSON value and return its normalized form.

    The returned children preserve source order; their ordinal is assigned by
    the caller.  The whole event fails on the first bad required child.
    """

    if not isinstance(obj, dict):
        raise EventProblem("/", "event must be a JSON object")

    if "id" not in obj:
        raise EventProblem("/id", "missing id")
    event_id = _require_non_empty_string(obj["id"], path="/id", what="id")

    if "rev" not in obj:
        raise EventProblem("/rev", "missing rev")
    rev = obj["rev"]
    if not _is_int(rev) or rev <= 0:
        raise EventProblem("/rev", "rev must be a positive integer")
    if rev > INT64_MAX:
        raise EventProblem("/rev", "rev is out of range")

    if "schema" not in obj:
        raise EventProblem("/schema", "missing schema")
    schema = obj["schema"]
    if not _is_int(schema) or schema not in (1, 2):
        raise EventProblem("/schema", "schema must be 1 or 2")

    if "device" not in obj:
        raise EventProblem("/device", "missing device")
    device = obj["device"]
    if not isinstance(device, str):
        raise EventProblem("/device", "device must be a string")

    if schema == 1:
        array_name = "readings"
        channel_key = "channel"
        value_key = "value"
    else:
        array_name = "samples"
        channel_key = "ch"
        value_key = "v"

    if array_name not in obj:
        state = "missing"
        children: List[Tuple[str, int]] = []
    elif obj[array_name] is None:
        state = "null"
        children = []
    elif not isinstance(obj[array_name], list):
        raise EventProblem(_path(array_name), "array must be a list or null")
    elif len(obj[array_name]) == 0:
        state = "empty"
        children = []
    else:
        state = "present"
        children = []
        for index, element in enumerate(obj[array_name]):
            element_path = _path(array_name, index)
            if element is None:
                raise EventProblem(element_path, "null element")
            if not isinstance(element, dict):
                raise EventProblem(element_path, "element must be an object")

            if channel_key not in element:
                raise EventProblem(
                    _path(array_name, index, channel_key), "missing channel"
                )
            channel = element[channel_key]
            if not isinstance(channel, str) or channel == "":
                raise EventProblem(
                    _path(array_name, index, channel_key),
                    "channel must be a non-empty string",
                )

            if value_key not in element:
                raise EventProblem(
                    _path(array_name, index, value_key), "missing value"
                )
            value = element[value_key]
            if not _is_int(value):
                raise EventProblem(
                    _path(array_name, index, value_key),
                    "value must be an integer",
                )
            if not (INT64_MIN <= value <= INT64_MAX):
                raise EventProblem(
                    _path(array_name, index, value_key),
                    "value is out of range",
                )

            children.append((channel, value))

    return {
        "id": event_id,
        "rev": rev,
        "device": device,
        "array_state": state,
        "children": children,
    }


# ---------------------------------------------------------------------------
# Reject rows
# ---------------------------------------------------------------------------


def _reject_row(
    *,
    event_id: Optional[str],
    rev: Optional[int],
    source: str,
    line: int,
    path: str,
    reason: str,
) -> Dict[str, Any]:
    return {
        "id": event_id,
        "rev": rev,
        "source": source,
        "line": int(line),
        "path": path,
        "reason": reason,
    }


def _reject_key(row: Dict[str, Any]) -> Tuple[Any, ...]:
    # A recorded rejection is a fact about a concrete source position and
    # failure.  Re-reading the same input therefore cannot add a second row.
    return (
        row.get("source"),
        row.get("line"),
        row.get("id"),
        row.get("rev"),
        row.get("path"),
        row.get("reason"),
    )


def _reject_sort_key(row: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        "" if row.get("source") is None else str(row.get("source")),
        -1 if row.get("line") is None else int(row.get("line")),
        "" if row.get("id") is None else str(row.get("id")),
        -1 if row.get("rev") is None else int(row.get("rev")),
        "" if row.get("path") is None else str(row.get("path")),
        "" if row.get("reason") is None else str(row.get("reason")),
    )


def _safe_reject_identity(obj: Any) -> Tuple[Optional[str], Optional[int]]:
    """Best-effort id/rev for a malformed event, for diagnostic purposes."""

    if not isinstance(obj, dict):
        return None, None
    raw_id = obj.get("id")
    event_id = raw_id if isinstance(raw_id, str) else None
    raw_rev = obj.get("rev")
    rev = raw_rev if _is_int(raw_rev) else None
    return event_id, rev


# ---------------------------------------------------------------------------
# Version storage
# ---------------------------------------------------------------------------


def _versions_dir(root: Path) -> Path:
    return root / "versions"


def _version_dir(root: Path, version: int) -> Path:
    return _versions_dir(root) / ("v%d" % version)


def _snapshot_files(directory: Path) -> Dict[str, Path]:
    return {
        "events": directory / "events.parquet",
        "readings": directory / "readings.parquet",
        "rejects": directory / "rejects.parquet",
    }


def _is_complete_version(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    files = _snapshot_files(directory)
    return all(path.is_file() for path in files.values())


def _published_versions(root: Path) -> List[int]:
    directory = _versions_dir(root)
    if not directory.is_dir():
        return []
    found: List[int] = []
    for child in directory.iterdir():
        match = _VERSION_DIR_RE.match(child.name)
        if not match:
            continue
        # A version directory is public only after all three tables exist.
        if _is_complete_version(child):
            found.append(int(match.group(1)))
    return sorted(found)


def _current_version(root: Path) -> int:
    versions = _published_versions(root)
    return versions[-1] if versions else 0


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _event_signature(
    device: Any, array_state: Any, children: Sequence[Tuple[Any, Any]]
) -> str:
    """Stable identity of an event's normalized payload.

    Two source objects that normalize to the same parent fields and the same
    ordered children are the same payload for the immutability rule.  This is
    deliberately derived from the columnar form so the signature can be
    recomputed from the published tables (used as a fallback when the private
    state sidecar is unavailable).
    """

    return _fingerprint(
        {
            "device": device,
            "array_state": array_state,
            "children": [[channel, value] for channel, value in children],
        }
    )


def _signature_from_rows(
    parent: Dict[str, Any], children: Sequence[Dict[str, Any]]
) -> str:
    ordered = sorted(children, key=lambda item: item.get("ordinal") or 0)
    return _event_signature(
        parent.get("device"),
        parent.get("array_state"),
        [(item.get("channel"), item.get("value")) for item in ordered],
    )


def _load_accepted(
    directory: Path,
    events: Optional[Sequence[Dict[str, Any]]] = None,
    readings: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[Tuple[str, int], str]:
    """Load accepted (id, rev) signatures from a version directory.

    ``state.json`` is private publication state, not part of the public table
    contract.  It records every revision that was ever accepted, including
    ones later superseded, which is required to reject a replay of an old
    revision with a different payload.  The currently published revision of
    each id is also seeded from the Parquet rows, so a store without the
    sidecar still enforces the immutability rule for visible events.
    """

    accepted: Dict[Tuple[str, int], str] = {}

    path = directory / "state.json"
    if path.is_file():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        entries = state.get("accepted", []) if isinstance(state, dict) else []
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                event_id = entry.get("id")
                rev = entry.get("rev")
                signature = entry.get("signature")
                if (
                    isinstance(event_id, str)
                    and _is_int(rev)
                    and isinstance(signature, str)
                ):
                    accepted[(event_id, rev)] = signature

    if events is None or readings is None:
        events, readings, _ = _load_rows(directory)

    by_identity: Dict[Tuple[Any, Any], List[Dict[str, Any]]] = {}
    for child in readings:
        by_identity.setdefault(
            (child.get("id"), child.get("rev")), []
        ).append(child)
    for parent in events:
        key = (parent.get("id"), parent.get("rev"))
        if key not in accepted:
            accepted[key] = _signature_from_rows(
                parent, by_identity.get(key, [])
            )

    return accepted


def _state_payload(accepted: Dict[Tuple[str, int], str]) -> Dict[str, Any]:
    entries = [
        {"id": event_id, "rev": rev, "signature": signature}
        for (event_id, rev), signature in sorted(accepted.items())
    ]
    return {"accepted": entries}


def _write_snapshot(
    directory: Path,
    *,
    events: Sequence[Dict[str, Any]],
    readings: Sequence[Dict[str, Any]],
    rejects: Sequence[Dict[str, Any]],
    accepted: Dict[Tuple[str, int], str],
) -> None:
    directory.mkdir(parents=True, exist_ok=False)
    _write_table(events, EVENTS_SCHEMA, directory / "events.parquet")
    _write_table(readings, READINGS_SCHEMA, directory / "readings.parquet")
    _write_table(rejects, REJECTS_SCHEMA, directory / "rejects.parquet")
    (directory / "state.json").write_text(
        _canonical_json(_state_payload(accepted)) + "\n", encoding="utf-8"
    )


def _load_rows(directory: Path) -> Tuple[
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
]:
    files = _snapshot_files(directory)
    events = _read_table(files["events"], EVENTS_SCHEMA)
    readings = _read_table(files["readings"], READINGS_SCHEMA)
    rejects = _read_table(files["rejects"], REJECTS_SCHEMA)
    return events, readings, rejects


def _read_snapshot(
    directory: Path,
) -> Tuple[
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    Dict[Tuple[str, int], str],
]:
    events, readings, rejects = _load_rows(directory)
    return events, readings, rejects, _load_accepted(directory, events, readings)


def _ensure_store(root: Path) -> None:
    """Create the initial (empty) version 0 exactly once, atomically.

    Version 0 is the initial published state.  It has real, empty Parquet
    files so ``inspect`` always returns usable paths, and so every later
    version's rows can be read without a missing-file special case.
    """

    if _published_versions(root):
        return
    directory = _version_dir(root, 0)
    directory.parent.mkdir(parents=True, exist_ok=True)
    if directory.exists():
        # A partially written version 0 was never published; rebuild it.
        shutil.rmtree(directory, ignore_errors=True)

    staging = directory.parent / (".v0-%s" % uuid.uuid4().hex)
    staging.mkdir(parents=True)
    try:
        _write_table([], EVENTS_SCHEMA, staging / "events.parquet")
        _write_table([], READINGS_SCHEMA, staging / "readings.parquet")
        _write_table([], REJECTS_SCHEMA, staging / "rejects.parquet")
        (staging / "state.json").write_text(
            _canonical_json(_state_payload({})) + "\n", encoding="utf-8"
        )
        os.rename(str(staging), str(directory))
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _cleanup_staging(root: Path) -> None:
    """Roll back any unpublished staging output.

    ``--crash-before-publish`` stops after every staged table has been written
    but before the atomic rename, so nothing under ``.staging`` was ever
    public.  Publication is all-or-nothing: recovery discards the interrupted
    snapshot entirely, and the next ingest rebuilds a deterministic snapshot
    from the last published version plus its own input.  Consequently a
    corrected re-send is compared against the *published* accepted state, not
    against a payload the crash never made public.
    """

    shutil.rmtree(root / ".staging", ignore_errors=True)


def _commit_snapshot(
    root: Path,
    staged_directory: Path,
    target_version: int,
) -> int:
    """Atomically make a fully written staging directory public."""

    destination = _version_dir(root, target_version)
    if destination.exists():
        # Never replace a published version.
        shutil.rmtree(staged_directory, ignore_errors=True)
        raise SystemExit("refusing to replace existing version %d" % target_version)
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.rename(str(staged_directory), str(destination))
    return target_version


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


class IngestResult:
    def __init__(
        self,
        *,
        version: int,
        changed: bool,
        staged_directory: Optional[Path] = None,
        target_version: Optional[int] = None,
    ) -> None:
        self.version = version
        self.changed = changed
        self.staged_directory = staged_directory
        self.target_version = target_version


def _parent_and_children(
    event: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Return the parent row and child rows for a normalized event."""

    parent = {
        "id": event["id"],
        "rev": int(event["rev"]),
        "device": event["device"],
        "array_state": event["array_state"],
    }
    children = [
        {
            "id": event["id"],
            "rev": int(event["rev"]),
            "ordinal": ordinal,
            "channel": channel,
            "value": int(value),
        }
        for ordinal, (channel, value) in enumerate(event["children"])
    ]
    return parent, children


def _process_source(
    root: Path,
    source: Path,
    *,
    crash_before_publish: bool,
) -> IngestResult:
    root.mkdir(parents=True, exist_ok=True)
    _ensure_store(root)
    _cleanup_staging(root)

    current_version = _current_version(root)
    if current_version:
        events, readings, rejects, accepted = _read_snapshot(
            _version_dir(root, current_version)
        )
    else:
        events, readings, rejects, accepted = [], [], [], {}

    # Public snapshots contain only one (highest) revision per id.
    selected_rev: Dict[str, int] = {}
    for row in events:
        event_id = row.get("id")
        rev = row.get("rev")
        if isinstance(event_id, str) and _is_int(rev):
            previous = selected_rev.get(event_id)
            if previous is None or rev > previous:
                selected_rev[event_id] = rev

    reject_keys = {_reject_key(row) for row in rejects}
    events = list(events)
    readings = list(readings)
    rejects = list(rejects)

    changed = current_version == 0

    try:
        source_text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit("cannot read source %s: %s" % (source, exc))

    source_name = str(source)

    for line_number, line in enumerate(source_text.splitlines(), start=1):
        # Blank separator lines are not events.  splitlines() already drops
        # the final empty fragment; line numbers still match the file.
        if line.strip() == "":
            continue

        try:
            obj = json.loads(line)
        except ValueError as exc:
            event_id, rev = None, None
            problem_path = "/"
            problem_reason = "invalid JSON: %s" % exc
        else:
            try:
                normalized = _validate_event(obj)
            except EventProblem as problem:
                event_id, rev = _safe_reject_identity(obj)
                problem_path = problem.path
                problem_reason = problem.reason
            else:
                event_id = normalized["id"]
                rev = normalized["rev"]
                signature = _event_signature(
                    normalized["device"],
                    normalized["array_state"],
                    normalized["children"],
                )
                identity = (event_id, rev)

                if identity in accepted:
                    if accepted[identity] != signature:
                        row = _reject_row(
                            event_id=event_id,
                            rev=rev,
                            source=source_name,
                            line=line_number,
                            path="/",
                            reason="same id/rev with different payload",
                        )
                        key = _reject_key(row)
                        if key not in reject_keys:
                            rejects.append(row)
                            reject_keys.add(key)
                            changed = True
                    # Exact replay is a no-op.
                    continue

                highest = selected_rev.get(event_id)
                if highest is not None:
                    if rev < highest:
                        # A valid but stale revision neither changes the
                        # public snapshot nor creates a rejection.
                        continue
                    if rev == highest:
                        # The (id, rev) identity is not in the accepted
                        # state, so an accepted public event at this revision
                        # must have a different payload.  Immutable: reject
                        # and keep the previously accepted event.
                        row = _reject_row(
                            event_id=event_id,
                            rev=rev,
                            source=source_name,
                            line=line_number,
                            path="/",
                            reason="same id/rev with different payload",
                        )
                        key = _reject_key(row)
                        if key not in reject_keys:
                            rejects.append(row)
                            reject_keys.add(key)
                            changed = True
                        continue

                # Accept this revision.  It becomes the selected parent and
                # its children replace any previous revision of this id.
                accepted[identity] = signature
                events = [row for row in events if row.get("id") != event_id]
                readings = [
                    row for row in readings if row.get("id") != event_id
                ]
                parent, children = _parent_and_children(normalized)
                events.append(parent)
                readings.extend(children)
                selected_rev[event_id] = rev
                changed = True
                continue

        # Invalid event: one reject row for the whole event.  The previous
        # accepted revision, if any, remains in the working snapshot.
        row = _reject_row(
            event_id=event_id,
            rev=rev,
            source=source_name,
            line=line_number,
            path=problem_path,
            reason=problem_reason,
        )
        key = _reject_key(row)
        if key not in reject_keys:
            rejects.append(row)
            reject_keys.add(key)
            changed = True

    # Deterministic public ordering.
    events.sort(key=lambda row: (row.get("id") or "", row.get("rev") or 0))
    readings.sort(
        key=lambda row: (
            row.get("id") or "",
            int(row.get("ordinal") or 0),
            int(row.get("rev") or 0),
        )
    )
    rejects.sort(key=_reject_sort_key)

    target_version = current_version + 1
    should_publish = changed or crash_before_publish

    if not should_publish:
        # Nothing changed and no crash was requested.  A pending snapshot that
        # is identical to the published one (if any) is stale; remove it.
        _cleanup_staging(root)
        return IngestResult(version=current_version, changed=False)

    # Always stage all three tables (and the private accepted-state sidecar)
    # before the atomic directory rename.  In crash mode this is exactly the
    # promised interruption point: complete staged output, no public version.
    staging_parent = root / ".staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    _cleanup_staging(root)
    staged_directory = staging_parent / (
        "v%d-%s" % (target_version, uuid.uuid4().hex)
    )
    _write_snapshot(
        staged_directory,
        events=events,
        readings=readings,
        rejects=rejects,
        accepted=accepted,
    )

    if crash_before_publish:
        # All three staged tables exist but the version is not published.
        # The process exits 97 here; the next ingest rolls this staging
        # directory back before rebuilding from the last published version.
        return IngestResult(
            version=current_version,
            changed=changed,
            staged_directory=staged_directory,
            target_version=target_version,
        )

    _commit_snapshot(root, staged_directory, target_version)
    _cleanup_staging(root)

    return IngestResult(version=target_version, changed=True)


# ---------------------------------------------------------------------------
# Inspect
# ---------------------------------------------------------------------------


def _inspect(root: Path, requested_version: Optional[int]) -> Dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    _ensure_store(root)

    if requested_version is None:
        version = _current_version(root)
    else:
        version = requested_version
        if version < 0:
            raise SystemExit("invalid version %d" % version)
        if not _is_complete_version(_version_dir(root, version)):
            raise SystemExit("version %d has not been published" % version)

    directory = _version_dir(root, version)
    paths = _snapshot_files(directory)
    files = {name: _relative(root, path) for name, path in paths.items()}
    return {"version": version, "files": files}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import device events into versioned Parquet tables."
    )
    parser.add_argument("root", type=Path)
    subparsers = parser.add_subparsers(dest="op", required=True)

    ingest = subparsers.add_parser("ingest")
    ingest.add_argument("source", type=Path)
    ingest.add_argument("--crash-before-publish", action="store_true")

    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--version", type=int)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.op == "ingest":
        result = _process_source(
            args.root,
            args.source,
            crash_before_publish=args.crash_before_publish,
        )
        if args.crash_before_publish:
            # The staged version is complete but intentionally not renamed
            # into the public version store.
            return 97
        print(json.dumps({"version": result.version}, sort_keys=True))
        return 0

    output = _inspect(args.root, args.version)
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
