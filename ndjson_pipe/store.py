"""SQLite persistence with a single-transaction invariant.

Accepted records, rejected records, content conflicts and the source
checkpoint always advance inside **one** database transaction. A crash before
commit therefore leaves the checkpoint at the last committed line, so a replay
of the original input reprocesses exactly the un-durable lines and converges
to the same final state -- never to duplicates.

Schema overview
---------------
``records``     accepted, normalized rows (exact decimal amount stored as TEXT)
``rejections``  structured rejection reasons plus the original raw bytes
``conflicts``   same (source, byte offset) seen again with different content
``checkpoints`` one durable row per source: next byte offset to process
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from .decoder import RawLine
from .normalize import Normalized, Reject

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    source_id        TEXT NOT NULL,
    line_no          INTEGER NOT NULL,
    start_offset     INTEGER NOT NULL,
    end_offset       INTEGER NOT NULL,
    content_hash     TEXT NOT NULL,
    record_id        TEXT NOT NULL,
    version          INTEGER NOT NULL,
    normalized_by    INTEGER NOT NULL,
    currency         TEXT NOT NULL,
    amount           TEXT NOT NULL,
    amount_cents     INTEGER NOT NULL,
    note_present     INTEGER NOT NULL,
    note_value       TEXT,
    raw_line         TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    PRIMARY KEY (source_id, start_offset)
);

CREATE TABLE IF NOT EXISTS rejections (
    source_id        TEXT NOT NULL,
    line_no          INTEGER NOT NULL,
    start_offset     INTEGER NOT NULL,
    end_offset       INTEGER NOT NULL,
    content_hash     TEXT NOT NULL,
    reason           TEXT NOT NULL,
    field            TEXT,
    detail           TEXT NOT NULL,
    raw_line         TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    PRIMARY KEY (source_id, start_offset)
);

CREATE TABLE IF NOT EXISTS conflicts (
    source_id        TEXT NOT NULL,
    start_offset     INTEGER NOT NULL,
    existing_kind    TEXT NOT NULL,
    existing_hash    TEXT NOT NULL,
    incoming_hash    TEXT NOT NULL,
    raw_line         TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    PRIMARY KEY (source_id, start_offset)
);

CREATE TABLE IF NOT EXISTS checkpoints (
    source_id        TEXT PRIMARY KEY,
    next_offset      INTEGER NOT NULL,
    last_line_no     INTEGER NOT NULL,
    updated_at       TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    """Thin persistence layer. Transaction boundaries are explicit."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- idempotency / conflict support -----------------------------------

    def existing_at(self, source_id: str, start_offset: int) -> Optional[Tuple[str, str]]:
        """Return ``(kind, content_hash)`` already stored at a source offset.

        ``kind`` is 'record', 'rejection' or 'conflict'. None means the
        position was never durably processed.
        """
        row = self.conn.execute(
            "SELECT 'record', content_hash FROM records "
            "WHERE source_id = ? AND start_offset = ?",
            (source_id, start_offset),
        ).fetchone()
        if row is not None:
            return row
        row = self.conn.execute(
            "SELECT 'rejection', content_hash FROM rejections "
            "WHERE source_id = ? AND start_offset = ?",
            (source_id, start_offset),
        ).fetchone()
        if row is not None:
            return row
        row = self.conn.execute(
            "SELECT 'conflict', incoming_hash FROM conflicts "
            "WHERE source_id = ? AND start_offset = ?",
            (source_id, start_offset),
        ).fetchone()
        return row

    def get_checkpoint(self, source_id: str) -> int:
        """Next byte offset to process (0 when the source is new)."""
        return self.get_checkpoint_ex(source_id)[0]

    def get_checkpoint_ex(self, source_id: str) -> Tuple[int, int]:
        """Return ``(next_offset, last_line_no)`` for resume positioning."""
        row = self.conn.execute(
            "SELECT next_offset, last_line_no FROM checkpoints WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        return (0, 0) if row is None else (int(row[0]), int(row[1]))

    # -- explicit transaction ---------------------------------------------

    def begin(self) -> None:
        self.conn.execute("BEGIN IMMEDIATE")

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    # -- mutating operations (valid only inside a transaction) ------------

    def insert_record(self, source_id: str, raw_line: RawLine, content_hash: str, n: Normalized) -> None:
        self.conn.execute(
            "INSERT INTO records (source_id, line_no, start_offset, end_offset, content_hash, "
            "record_id, version, normalized_by, currency, amount, amount_cents, note_present, "
            "note_value, raw_line, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                source_id,
                raw_line.line_no,
                raw_line.start_offset,
                raw_line.end_offset,
                content_hash,
                n.record_id,
                n.version,
                n.normalized_by,
                n.currency,
                str(n.amount),
                n.amount_cents,
                1 if n.note_present else 0,
                n.note_value,
                raw_line.raw.decode("utf-8", errors="replace"),
                _now(),
            ),
        )

    def insert_rejection(self, source_id: str, raw_line: RawLine, content_hash: str, reject: Reject) -> None:
        self.conn.execute(
            "INSERT INTO rejections (source_id, line_no, start_offset, end_offset, content_hash, "
            "reason, field, detail, raw_line, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                source_id,
                raw_line.line_no,
                raw_line.start_offset,
                raw_line.end_offset,
                content_hash,
                reject.reason,
                reject.field,
                reject.detail,
                raw_line.raw.decode("utf-8", errors="replace"),
                _now(),
            ),
        )

    def insert_conflict(
        self,
        source_id: str,
        raw_line: RawLine,
        existing_kind: str,
        existing_hash: str,
        incoming_hash: str,
    ) -> None:
        self.conn.execute(
            "INSERT INTO conflicts (source_id, start_offset, existing_kind, existing_hash, "
            "incoming_hash, raw_line, created_at) VALUES (?,?,?,?,?,?,?)",
            (
                source_id,
                raw_line.start_offset,
                existing_kind,
                existing_hash,
                incoming_hash,
                raw_line.raw.decode("utf-8", errors="replace"),
                _now(),
            ),
        )

    def advance_checkpoint(self, source_id: str, next_offset: int, last_line_no: int) -> None:
        # The checkpoint is the largest durably processed byte position; it is
        # monotonic. A full-scan redelivery of an equal or shorter file must
        # never move it backwards, while a normal append simply advances it.
        self.conn.execute(
            "INSERT INTO checkpoints (source_id, next_offset, last_line_no, updated_at) "
            "VALUES (?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET "
            "next_offset = MAX(checkpoints.next_offset, excluded.next_offset), "
            "last_line_no = MAX(checkpoints.last_line_no, excluded.last_line_no), "
            "updated_at = excluded.updated_at",
            (source_id, next_offset, last_line_no, _now()),
        )

    # -- verification helpers ---------------------------------------------

    def dump_records(self, source_id: str) -> List[tuple]:
        return [tuple(r) for r in self.conn.execute(
            "SELECT line_no, start_offset, end_offset, record_id, version, normalized_by, "
            "currency, amount, amount_cents, note_present, note_value, content_hash "
            "FROM records WHERE source_id = ? ORDER BY start_offset", (source_id,)
        )]

    def dump_rejections(self, source_id: str) -> List[tuple]:
        return [tuple(r) for r in self.conn.execute(
            "SELECT line_no, start_offset, end_offset, reason, field, content_hash "
            "FROM rejections WHERE source_id = ? ORDER BY start_offset", (source_id,)
        )]

    def dump_conflicts(self, source_id: str) -> List[tuple]:
        return [tuple(r) for r in self.conn.execute(
            "SELECT start_offset, existing_kind, existing_hash, incoming_hash "
            "FROM conflicts WHERE source_id = ? ORDER BY start_offset", (source_id,)
        )]

    def stats(self, source_id: str) -> dict:
        def count(table: str) -> int:
            return self.conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE source_id = ?", (source_id,)
            ).fetchone()[0]

        return {
            "records": count("records"),
            "rejections": count("rejections"),
            "conflicts": count("conflicts"),
            "checkpoint": self.get_checkpoint(source_id),
        }
