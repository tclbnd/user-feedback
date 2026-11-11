"""Streaming importer: read a source in chunks from its checkpoint, split
into lines, normalize, and persist records/rejects/checkpoint atomically.

Crash simulation for acceptance: if the environment variable
NDJSON_KILL_POINT is set to one of
  after_parse     kill the process after a line is parsed, before any write
  in_transaction  kill after the batch rows are written, before COMMIT
  after_commit    kill right after COMMIT, before the next batch
the process hard-exits (os._exit) at the first batch, like a SIGKILL.
NDJSON_NORM_VERSION overrides the normalization version (default:
normalizer.CURRENT_NORM_VERSION).
"""

import hashlib
import os

from .normalizer import CURRENT_NORM_VERSION, normalize_line
from .stream import ByteLineSplitter


def _sha(data):
    return hashlib.sha256(data).hexdigest()


class IngestStats:
    def __init__(self):
        self.records = 0
        self.rejects = 0
        self.skips = 0
        self.conflicts = 0

    def as_dict(self):
        return {"records": self.records, "rejects": self.rejects,
                "skips": self.skips, "conflicts": self.conflicts}


def _handle_line(store, source_id, offset, raw, norm_version, stats):
    sha = _sha(raw)
    existing = store.get_row(source_id, offset)
    if existing is not None:
        kind, existing_sha = existing
        if existing_sha == sha:
            stats.skips += 1
            return
        store.insert_conflict(source_id, offset, kind, existing_sha, sha, raw)
        stats.conflicts += 1
        return
    result = normalize_line(raw, norm_version)
    if result.ok:
        store.insert_record(source_id, offset, sha, raw, norm_version, result)
        stats.records += 1
    else:
        store.insert_reject(source_id, offset, sha, raw,
                            result.reason_code, result.detail)
        stats.rejects += 1


def _commit_batch(store, source_id, lines, offset, norm_version, kill_point,
                  killed, stats):
    """Persist one batch of newline-terminated lines plus the checkpoint
    advance atomically."""
    store.begin()
    try:
        pos = offset
        for raw in lines:
            result = normalize_line(raw, norm_version)  # parse first ...
            if kill_point == "after_parse" and not killed[0]:
                killed[0] = True
                store.rollback()
                os._exit(42)
            # ... then write (re-classified inside _handle_line only when
            # the offset is new; existing offsets are skip/conflict).
            sha = _sha(raw)
            existing = store.get_row(source_id, pos)
            if existing is not None:
                kind, existing_sha = existing
                if existing_sha == sha:
                    stats.skips += 1
                else:
                    store.insert_conflict(source_id, pos, kind, existing_sha,
                                          sha, raw)
                    stats.conflicts += 1
            elif result.ok:
                store.insert_record(source_id, pos, sha, raw, norm_version,
                                    result)
                stats.records += 1
            else:
                store.insert_reject(source_id, pos, sha, raw,
                                    result.reason_code, result.detail)
                stats.rejects += 1
            pos += len(raw) + 1  # every line here is newline-terminated
        if kill_point == "in_transaction" and not killed[0]:
            killed[0] = True
            store.rollback()
            os._exit(42)
        store.set_checkpoint(source_id, pos, 0)
        store.commit()
    except BaseException:
        try:
            store.rollback()
        except Exception:
            pass
        raise
    if kill_point == "after_commit" and not killed[0]:
        killed[0] = True
        os._exit(42)
    return pos


def ingest(store, source_id, path, chunk_size=65536, seal=False,
           norm_version=None):
    """Ingest new bytes of `path` for `source_id`, starting at the stored
    checkpoint.  With seal=True the trailing partial line (if any) is
    finalized and the source is marked sealed.  Returns IngestStats."""
    if norm_version is None:
        norm_version = int(os.environ.get("NDJSON_NORM_VERSION",
                                          CURRENT_NORM_VERSION))
    kill_point = os.environ.get("NDJSON_KILL_POINT")
    killed = [False]
    stats = IngestStats()

    ckpt = store.get_checkpoint(source_id)
    if ckpt is not None and ckpt[1]:
        return stats  # sealed source: replay is a no-op
    offset = ckpt[0] if ckpt else 0

    splitter = ByteLineSplitter()
    with open(path, "rb") as fh:
        fh.seek(offset)
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            lines = splitter.feed(chunk)
            if lines:
                offset = _commit_batch(store, source_id, lines, offset,
                                       norm_version, kill_point, killed,
                                       stats)
    if seal:
        tail = splitter.tail()
        if tail:
            # Final unterminated line: it ends at EOF, no newline follows.
            end = offset + len(tail)
            store.begin()
            try:
                _handle_line(store, source_id, offset, tail, norm_version,
                             stats)
                store.set_checkpoint(source_id, end, 1)
                store.commit()
            except BaseException:
                try:
                    store.rollback()
                except Exception:
                    pass
                raise
        else:
            store.begin()
            try:
                store.set_checkpoint(source_id, offset, 1)
                store.commit()
            except BaseException:
                try:
                    store.rollback()
                except Exception:
                    pass
                raise
    return stats
