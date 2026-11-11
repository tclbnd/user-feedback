"""Shared helpers for pipeline / differential tests."""

import os
import tempfile
from typing import List, Optional

from ndjson_pipe.importer import CrashPlan, import_source
from ndjson_pipe.store import Store


def make_db_path() -> str:
    fd, path = tempfile.mkstemp(prefix="ndjson-pipe-", suffix=".db")
    os.close(fd)
    os.unlink(path)  # Store creates the file; start from a clean path
    return path


def write_source(path: str, data: bytes) -> None:
    with open(path, "wb") as fh:
        fh.write(data)


def append_source(path: str, data: bytes) -> None:
    with open(path, "ab") as fh:
        fh.write(data)


def run_chunked(
    db_path: str,
    source_id: str,
    data: bytes,
    chunk_sizes: List[int],
    *,
    sealed: bool = True,
    batch_size: int = 200,
    full_scan: bool = False,
    crash_plan: Optional[CrashPlan] = None,
):
    """Drive one import through the production path with explicit chunk sizes."""
    path = db_path + ".ndjson"
    write_source(path, data)
    with Store(db_path) as store:
        return import_source(
            store,
            source_id,
            path,
            sealed=sealed,
            batch_size=batch_size,
            full_scan=full_scan,
            crash_plan=crash_plan or CrashPlan(),
            chunk_sizes=chunk_sizes,
        )


def run_appends(
    db_path: str,
    source_id: str,
    parts: List[bytes],
    chunk_size: int = 64,
    *,
    sealed_flags: Optional[List[bool]] = None,
    crash_plan: Optional[CrashPlan] = None,
):
    """Reveal one logical source through true byte appends.

    Each round physically appends only the new bytes to the file; the
    importer resumes from its checkpoint. The final logical source is the
    concatenation of all parts.
    """
    path = db_path + ".ndjson"
    # Fresh logical source: remove any file left by an earlier test case.
    if os.path.exists(path):
        os.unlink(path)
    sealed_flags = sealed_flags or [False] * (len(parts) - 1) + [True]
    assert len(sealed_flags) == len(parts)
    last_stats = None
    with Store(db_path) as store:
        for part, sealed in zip(parts, sealed_flags):
            append_source(path, part)
            last_stats = import_source(
                store,
                source_id,
                path,
                sealed=sealed,
                chunk_size=chunk_size,
                crash_plan=crash_plan or CrashPlan(),
            )
    return last_stats
