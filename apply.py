"""Replica store and transaction applier.

Durability model
----------------
Everything that must survive a crash -- the tables, the applied commit
position and the conflict log -- is kept in one file, ``journal.json``, which
is replaced atomically.  That single ``os.replace`` is the commit point of a
local transaction: data, position and conflict log become visible together or
not at all.

``replica.json``, ``conflicts.jsonl`` and ``position.json`` are renderings of
the journal in the format the task defines.  They are rewritten after the
journal, in that order, so the position file never claims more than the data
and the conflict log already contain.  If the process dies while rendering
them, the next run loads the journal (the source of truth) and re-renders
them before it streams anything.
"""
import copy
import json
import os


def _fsync_dir(directory):
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_json(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(os.path.dirname(path) or ".")


def write_lines(path, entries):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(os.path.dirname(path) or ".")


def row_sort_key(value):
    """Deterministic order for primary keys: numbers, strings, nulls."""
    if isinstance(value, bool):
        return (2, str(value))
    if isinstance(value, (int, float)):
        return (0, value)
    if value is None:
        return (3, "")
    return (1, str(value))


class Replica:
    def __init__(self, directory, on_commit=None):
        self.dir, self.on_commit = directory, on_commit
        os.makedirs(directory, exist_ok=True)
        self.path = os.path.join(directory, "replica.json")
        self.position_path = os.path.join(directory, "position.json")
        self.conflict_path = os.path.join(directory, "conflicts.jsonl")
        self.journal_path = os.path.join(directory, "journal.json")

        if os.path.exists(self.journal_path):
            with open(self.journal_path, encoding="utf-8") as fh:
                state = json.load(fh)
            tables = state.get("tables", {})
            self.position = state.get("commit_lsn", 0)
            self.conflicts = list(state.get("conflicts", []))
            self.tables = self._index(tables)
        else:
            # First run, or an upgrade from the file-per-item layout: adopt
            # whatever replica/position/conflict files exist and journal them.
            tables, self.position = self._load_legacy()
            self.conflicts = self._load_legacy_conflicts()
            self.tables = self._index(tables)
            self._write_journal()
        # Heal derived views in case a previous run died while rendering them.
        self._materialize()

    # -- loading ---------------------------------------------------------
    def _load_legacy(self):
        tables, position = {}, 0
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as fh:
                tables = json.load(fh).get("tables", {})
        if os.path.exists(self.position_path):
            with open(self.position_path, encoding="utf-8") as fh:
                position = json.load(fh).get("commit_lsn", 0)
        return tables, position

    def _load_legacy_conflicts(self):
        entries = []
        if os.path.exists(self.conflict_path):
            with open(self.conflict_path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        return entries

    @staticmethod
    def _index(tables):
        """{name: {primary_key, columns, rows: {serialized_pk: row}}}"""
        indexed = {}
        for name, t in tables.items():
            indexed[name] = {
                "primary_key": t["primary_key"],
                "columns": dict(t["columns"]),
                "rows": {json.dumps(r[t["primary_key"]]): dict(r) for r in t["rows"]},
            }
        return indexed

    # -- serialization ---------------------------------------------------
    def _render_tables(self, tables=None):
        tables = self.tables if tables is None else tables
        out = {}
        for name, t in tables.items():
            pk = t["primary_key"]
            rows = sorted(t["rows"].values(), key=lambda r: row_sort_key(r.get(pk)))
            out[name] = {"primary_key": pk, "columns": dict(t["columns"]), "rows": rows}
        return out

    def _write_journal(self):
        write_json(self.journal_path, {
            "commit_lsn": self.position,
            "tables": self._render_tables(),
            "conflicts": list(self.conflicts),
        })

    def _materialize(self):
        # Order matters: the position file is the published commit marker, so
        # it is written last -- it never gets ahead of data or conflict log.
        write_json(self.path, {"tables": self._render_tables()})
        write_lines(self.conflict_path, self.conflicts)
        write_json(self.position_path, {"commit_lsn": self.position})

    # -- commit ----------------------------------------------------------
    def rewind(self, commit_lsn):
        """Move the stored position back to an earlier commit boundary."""
        self.position = commit_lsn
        self._write_journal()
        self._materialize()

    def commit(self, tables, commit_lsn, conflicts):
        """Atomically publish a whole transaction: data + position + log.

        The journal write is the commit point.  In-memory state is only
        adopted once it succeeded, so a failed write leaves the object on the
        previous transaction, exactly like the files on disk.
        """
        merged = self.conflicts + list(conflicts)
        write_json(self.journal_path, {
            "commit_lsn": commit_lsn,
            "tables": self._render_tables(tables),
            "conflicts": merged,
        })
        self.tables = tables
        self.position = commit_lsn
        self.conflicts = merged
        self._materialize()
        if self.on_commit:
            self.on_commit()


class Applier:
    """Applies one transaction at a time onto a staged copy of the replica.

    Nothing touches the durable state until ``commit``; a connection that
    drops mid-transaction leaves the replica exactly as it was.
    """

    def __init__(self, replica):
        self.replica = replica
        self.position = replica.position
        self.xid = None
        self.staged = None
        self.conflicts = []

    def receive(self, m):
        """Handle one message; return the commit LSN once a transaction is applied."""
        kind = m["type"]

        if kind == "begin":
            self.xid = m["xid"]
            self.staged = copy.deepcopy(self.replica.tables)
            self.conflicts = []
            return None

        if kind == "commit":
            commit_lsn = m["lsn"]
            staged, conflicts = self.staged, self.conflicts
            self.xid, self.staged, self.conflicts = None, None, []
            if staged is None or commit_lsn <= self.position:
                # Already-applied transaction delivered again: skip it whole.
                return None
            logged = [dict(c, commit_lsn=commit_lsn) for c in conflicts]
            self.replica.commit(staged, commit_lsn, logged)
            self.position = commit_lsn
            return commit_lsn

        if self.staged is None:
            # The durable position is always a commit LSN, so a stream resume
            # starts at a `begin`.  Anything else means the stored position is
            # not a transaction boundary; applying the tail would silently
            # skip the head of this transaction, so refuse instead.
            raise RuntimeError(
                f"stream resumed mid-transaction at lsn {m['lsn']} "
                f"(type {kind!r}); position is not a commit boundary")

        if kind == "ddl":
            _apply_ddl(self.staged, m)
        elif kind in ("insert", "update", "delete"):
            self._apply_data(m)
        else:
            raise ValueError(f"unknown change type {kind!r} at lsn {m['lsn']}")
        return None

    # -- individual changes ---------------------------------------------
    def _apply_data(self, m):
        table = self.staged.get(m["table"])
        if table is None:
            raise KeyError(f"change for unknown table {m['table']!r} at lsn {m['lsn']}")
        pk = table["primary_key"]
        rows = table["rows"]
        op = m["type"]

        if op == "delete":
            key = json.dumps(m["key"])
            if key not in rows:
                self._conflict("delete_missing", m, m["key"])
                return
            del rows[key]
            return

        if op == "update":
            key = json.dumps(m["key"])
            if key not in rows:
                self._conflict("update_missing", m, m["key"])
                return
            row = {c: m["row"].get(c) for c in table["columns"]}
            new_key = json.dumps(row[pk])
            if new_key != key:
                del rows[key]
            rows[new_key] = row
            return

        # insert
        row = {c: m["row"].get(c) for c in table["columns"]}
        key = json.dumps(row[pk])
        if key in rows:
            self._conflict("insert_exists", m, row[pk])
        rows[key] = row

    def _conflict(self, kind, m, key):
        self.conflicts.append({
            "commit_lsn": None,  # filled in when the transaction commits
            "lsn": m["lsn"],
            "type": kind,
            "table": m["table"],
            "key": key,
        })


def _apply_ddl(tables, m):
    """Apply a DDL message in its exact place among the transaction's changes."""
    if m["op"] == "create_table":
        tables[m["table"]] = {
            "primary_key": m["primary_key"],
            "columns": dict(m["columns"]),
            "rows": {},
        }
        return
    table = tables.get(m["table"])
    if table is None:
        raise KeyError(f"ddl for unknown table {m['table']!r} at lsn {m['lsn']}")
    if m["op"] == "add_column":
        table["columns"][m["column"]] = m.get("default")
        for row in table["rows"].values():
            row[m["column"]] = m.get("default")
    elif m["op"] == "drop_column":
        table["columns"].pop(m["column"], None)
        for row in table["rows"].values():
            row.pop(m["column"], None)
    else:
        raise ValueError(f"unknown ddl op {m['op']!r} at lsn {m['lsn']}")

