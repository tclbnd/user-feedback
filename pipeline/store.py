"""SQLite storage: records, rejects, conflicts and checkpoints.

Every ingest batch writes its records/rejects and the checkpoint advance in
ONE transaction, so a crash can never persist data without its checkpoint
(or vice versa).  Rows are keyed by (source_id, byte_offset); replaying a
source position with identical bytes is a no-op, with different bytes it
produces a conflict row and leaves the original untouched.
"""

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS records(
  source_id    TEXT    NOT NULL,
  byte_offset  INTEGER NOT NULL,
  raw_sha256   TEXT    NOT NULL,
  raw_bytes    BLOB    NOT NULL,
  norm_version INTEGER NOT NULL,
  rec_id       TEXT    NOT NULL,
  amount       TEXT    NOT NULL,  -- exact decimal, 2 fractional digits
  currency     TEXT    NOT NULL,
  note_state   TEXT    NOT NULL,  -- present | null | missing
  note         TEXT,
  PRIMARY KEY (source_id, byte_offset)
);
CREATE TABLE IF NOT EXISTS rejects(
  source_id    TEXT    NOT NULL,
  byte_offset  INTEGER NOT NULL,
  raw_sha256   TEXT    NOT NULL,
  raw_bytes    BLOB    NOT NULL,
  reason_code  TEXT    NOT NULL,
  detail       TEXT    NOT NULL,
  PRIMARY KEY (source_id, byte_offset)
);
CREATE TABLE IF NOT EXISTS conflicts(
  seq             INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id       TEXT    NOT NULL,
  byte_offset     INTEGER NOT NULL,
  existing_kind   TEXT    NOT NULL,  -- record | reject
  existing_sha256 TEXT    NOT NULL,
  incoming_sha256 TEXT    NOT NULL,
  incoming_raw    BLOB    NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoints(
  source_id   TEXT    PRIMARY KEY,
  next_offset INTEGER NOT NULL,
  sealed      INTEGER NOT NULL DEFAULT 0
);
"""


class Store:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.isolation_level = None  # explicit BEGIN/COMMIT
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    # -- transactions -------------------------------------------------
    def begin(self):
        self.conn.execute("BEGIN IMMEDIATE")

    def commit(self):
        self.conn.execute("COMMIT")

    def rollback(self):
        self.conn.execute("ROLLBACK")

    # -- checkpoints --------------------------------------------------
    def get_checkpoint(self, source_id):
        row = self.conn.execute(
            "SELECT next_offset, sealed FROM checkpoints WHERE source_id=?",
            (source_id,)).fetchone()
        return (row[0], row[1]) if row else None

    def set_checkpoint(self, source_id, next_offset, sealed):
        self.conn.execute(
            "INSERT INTO checkpoints(source_id, next_offset, sealed)"
            " VALUES(?,?,?)"
            " ON CONFLICT(source_id) DO UPDATE SET next_offset=excluded.next_offset,"
            " sealed=excluded.sealed",
            (source_id, next_offset, sealed))

    def reset_checkpoint(self, source_id, next_offset):
        """Restore an older checkpoint (unseals the source).  Existing rows
        are kept; replay from this offset skips identical content and logs
        conflicts for differing content."""
        self.conn.execute(
            "INSERT INTO checkpoints(source_id, next_offset, sealed)"
            " VALUES(?,?,0)"
            " ON CONFLICT(source_id) DO UPDATE SET next_offset=excluded.next_offset,"
            " sealed=0",
            (source_id, next_offset))

    # -- rows -----------------------------------------------------------
    def get_row(self, source_id, byte_offset):
        row = self.conn.execute(
            "SELECT raw_sha256 FROM records WHERE source_id=? AND byte_offset=?",
            (source_id, byte_offset)).fetchone()
        if row:
            return ("record", row[0])
        row = self.conn.execute(
            "SELECT raw_sha256 FROM rejects WHERE source_id=? AND byte_offset=?",
            (source_id, byte_offset)).fetchone()
        if row:
            return ("reject", row[0])
        return None

    def insert_record(self, source_id, byte_offset, sha, raw, norm_version,
                      result):
        self.conn.execute(
            "INSERT INTO records(source_id, byte_offset, raw_sha256, raw_bytes,"
            " norm_version, rec_id, amount, currency, note_state, note)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (source_id, byte_offset, sha, raw, norm_version, result.rec_id,
             str(result.amount), result.currency, result.note_state,
             result.note))

    def insert_reject(self, source_id, byte_offset, sha, raw, reason, detail):
        self.conn.execute(
            "INSERT INTO rejects(source_id, byte_offset, raw_sha256, raw_bytes,"
            " reason_code, detail) VALUES(?,?,?,?,?,?)",
            (source_id, byte_offset, sha, raw, reason, detail))

    def insert_conflict(self, source_id, byte_offset, existing_kind,
                        existing_sha, incoming_sha, incoming_raw):
        self.conn.execute(
            "INSERT INTO conflicts(source_id, byte_offset, existing_kind,"
            " existing_sha256, incoming_sha256, incoming_raw)"
            " VALUES(?,?,?,?,?,?)",
            (source_id, byte_offset, existing_kind, existing_sha,
             incoming_sha, incoming_raw))

    # -- reads for verification ----------------------------------------
    def iter_records(self, source_id):
        return self.conn.execute(
            "SELECT byte_offset, norm_version, rec_id, amount, currency,"
            " note_state, note FROM records WHERE source_id=?"
            " ORDER BY byte_offset", (source_id,))

    def iter_rejects(self, source_id):
        return self.conn.execute(
            "SELECT byte_offset, reason_code, detail FROM rejects"
            " WHERE source_id=? ORDER BY byte_offset", (source_id,))

    def iter_conflicts(self, source_id):
        return self.conn.execute(
            "SELECT byte_offset, existing_kind, existing_sha256,"
            " incoming_sha256 FROM conflicts WHERE source_id=?"
            " ORDER BY seq", (source_id,))

    def count(self, table, source_id):
        return self.conn.execute(
            "SELECT COUNT(*) FROM %s WHERE source_id=?" % table,
            (source_id,)).fetchone()[0]

    def dump(self, source_id):
        """Canonical text dump of everything for a source (used to compare
        runs byte-for-byte)."""
        out = []
        for (off, nv, rid, amount, cur, ns, note) in self.iter_records(source_id):
            out.append("R|%d|%d|%s|%s|%s|%s|%s"
                       % (off, nv, rid, amount, cur, ns,
                          "" if note is None else note))
        for (off, reason, detail) in self.iter_rejects(source_id):
            out.append("J|%d|%s|%s" % (off, reason, detail))
        for (off, kind, esha, isha) in self.iter_conflicts(source_id):
            out.append("C|%d|%s|%s|%s" % (off, kind, esha, isha))
        ckpt = self.get_checkpoint(source_id)
        out.append("K|%d|%d" % (ckpt[0], ckpt[1]) if ckpt else "K|none")
        return "\n".join(out) + "\n"
