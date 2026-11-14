"""Local manifest table with content-addressed data and SQLite metadata.

Concurrency model
-----------------
All metadata mutations take one SQLite ``BEGIN IMMEDIATE`` write lock, so they
are totally ordered.  A compaction never overwrites an existing manifest: it
validates its frozen inputs against the current head inside that lock, rebases
when safe, and publishes a brand-new snapshot.  Old snapshots stay readable
until GC proves that no retained reader (head, pin, or an in-flight plan
baseline) references them.

Data objects are immutable and content-addressed.  They are staged in a
``.tmp`` sibling and atomically renamed, so a reader never observes a
half-written object; a crash before publish leaves only an unreferenced object
that GC is allowed to reclaim.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

CRASH_EXIT = 97
TMP_SUFFIX = ".tmp"


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class Lake:
    def __init__(self, root):
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.objects.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "catalog.db", timeout=10,
                                  isolation_level=None)
        self._init_schema()

    def _init_schema(self):
        # executescript issues an implicit COMMIT, so run DDL first and seed
        # data separately.  The seed rows belong to a brand-new catalog only:
        # re-inserting them on every connection would resurrect snapshot 0
        # after GC had legitimately pruned it.
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots (
                version INTEGER PRIMARY KEY,
                files TEXT NOT NULL,
                deleted TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS current (id INTEGER PRIMARY KEY, version INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS pins (version INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS plans (
                name TEXT PRIMARY KEY,
                base INTEGER NOT NULL,
                files TEXT NOT NULL,
                outcome TEXT
            );
        """)
        fresh = self.db.execute(
            "SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0
        if fresh:
            self.db.execute(
                "INSERT INTO snapshots VALUES (0, '[]', '[]')")
            self.db.execute("INSERT INTO current VALUES (0, 0)")
        # Forward migration for catalogs created by the previous implementation.
        cols = {row[1] for row in self.db.execute("PRAGMA table_info(plans)")}
        if "outcome" not in cols:
            self.db.execute("ALTER TABLE plans ADD COLUMN outcome TEXT")

    # ------------------------------------------------------------------ core
    def snapshot(self, version=None):
        if version is None:
            version = self.db.execute(
                "SELECT version FROM current WHERE id=0").fetchone()[0]
        row = self.db.execute(
            "SELECT files,deleted FROM snapshots WHERE version=?",
            (version,)).fetchone()
        if row is None:
            raise ValueError("unknown snapshot")
        return {"version": version,
                "files": json.loads(row[0]),
                "deleted": json.loads(row[1])}

    def get_rows(self, name):
        return json.loads((self.objects / (name + ".json")).read_text())

    def put_rows(self, rows):
        """Write an immutable object atomically and return its content hash."""
        payload = encode(rows)
        name = hashlib.sha256(payload.encode()).hexdigest()
        final = self.objects / (name + ".json")
        if not final.exists():
            tmp = self.objects / (name + ".json" + TMP_SUFFIX)
            tmp.write_text(payload)
            os.replace(tmp, final)
        return name

    def publish(self, files, deleted):
        """Insert a new snapshot and advance head. Caller holds the write lock."""
        version = self.snapshot()["version"] + 1
        self.db.execute(
            "INSERT INTO snapshots VALUES (?,?,?)",
            (version, encode(files), encode(sorted(deleted))))
        self.db.execute("UPDATE current SET version=? WHERE id=0", (version,))
        return version

    def _crash(self, point):
        # Hard exit: bypass buffered cleanup and implicit rollback handlers.
        sys.stderr.write("injected crash: " + point + "\n")
        sys.stderr.flush()
        os._exit(CRASH_EXIT)

    class _Txn:
        """Explicit transaction so commit points are visible in the code.

        SQLite's connection context manager would commit on block exit, which
        is too late for crash points that must fire *after* a durable commit.
        """

        def __init__(self, db):
            self.db = db
            self.closed = False

        def __enter__(self):
            self.db.execute("BEGIN IMMEDIATE")
            return self

        def __exit__(self, exc_type, exc, tb):
            if self.closed:
                return False
            if exc_type is None:
                self.db.execute("COMMIT")
            else:
                self.db.execute("ROLLBACK")
            return False

        def commit(self):
            self.db.execute("COMMIT")
            self.closed = True

        def rollback(self):
            self.db.execute("ROLLBACK")
            self.closed = True

    def _txn(self):
        return self._Txn(self.db)

    # -------------------------------------------------------------- writes
    def append(self, rows):
        if not isinstance(rows, list) or any("id" not in row for row in rows):
            raise ValueError("append expects a list of row objects with id")
        new_file = self.put_rows(rows)  # immutable: safe before the txn
        with self._txn():
            snap = self.snapshot()
            version = self.publish([*snap["files"], new_file], snap["deleted"])
        return {"status": "appended", "version": version, "file": new_file}

    def delete(self, ids):
        if not isinstance(ids, list):
            raise ValueError("delete expects a list of row ids")
        with self._txn():
            snap = self.snapshot()
            version = self.publish(
                snap["files"], set(snap["deleted"]).union(ids))
        return {"status": "deleted", "version": version}

    def read(self, version):
        snap = self.snapshot(version)
        deleted = set(snap["deleted"])
        rows = [row for name in snap["files"]
                for row in self.get_rows(name)
                if row["id"] not in deleted]
        return {"rows": sorted(rows, key=lambda row: row["id"]), **snap}

    # ------------------------------------------------------------ compaction
    def prepare(self, name):
        with self._txn():
            snap = self.snapshot()
            files = snap["files"][:2]
            if len(files) < 2:
                raise ValueError("at least two files required")
            # Same name always returns the same plan; inputs freeze here.
            self.db.execute(
                "INSERT OR IGNORE INTO plans(name,base,files,outcome) "
                "VALUES (?,?,?,NULL)",
                (name, snap["version"], encode(files)))
            base, selected, outcome = self.db.execute(
                "SELECT base,files,outcome FROM plans WHERE name=?",
                (name,)).fetchone()
        result = {"status": "prepared", "name": name,
                  "base": base, "files": json.loads(selected)}
        if outcome:
            result["outcome"] = json.loads(outcome)
        return result

    def commit(self, name, crash=None):
        # Phase 1: load the frozen plan.  Rows with an outcome are immutable.
        plan = self.db.execute(
            "SELECT base,files,outcome FROM plans WHERE name=?",
            (name,)).fetchone()
        if plan is None:
            raise ValueError("unknown plan: " + name)
        base, selected_raw, outcome = plan
        if outcome is not None:
            # Idempotent retry: a finished plan never publishes again.
            return json.loads(outcome)

        with self._txn() as txn:
            # Re-read under the lock so the decision is linearizable with
            # every append/delete/compaction that committed before us.
            plan = self.db.execute(
                "SELECT base,files,outcome FROM plans WHERE name=?",
                (name,)).fetchone()
            base, selected_raw, outcome = plan
            if outcome is not None:
                return json.loads(outcome)
            selected = json.loads(selected_raw)
            base_snap = self.snapshot(base)
            head = self.snapshot()

            # Validation 1: every selected file must still be a live input at
            # the current head.  Another compaction replacing either file is a
            # hard conflict.
            if any(f not in head["files"] for f in selected):
                result = {"status": "conflict", "version": head["version"],
                          "reason": "selected-file-replaced"}
                self._finish_plan(name, result)
                return result

            # Validation 2: the delete view over selected rows is frozen at
            # prepare time.  New deletes touching those rows change row
            # identity/visibility and force abort; deletes of other files are
            # safe to rebase over.
            selected_rows = {row["id"] for f in selected
                             for row in self.get_rows(f)}
            new_deletes = set(head["deleted"]) - set(base_snap["deleted"])
            if new_deletes & selected_rows:
                result = {"status": "conflict", "version": head["version"],
                          "reason": "conflicting-delete",
                          "deletes": sorted(new_deletes & selected_rows)}
                self._finish_plan(name, result)
                return result

            # Rebase: appends after baseline (and deletes outside the selected
            # files) are preserved because we compute against the head.
            merged = [row for f in selected for row in self.get_rows(f)
                      if row["id"] not in head["deleted"]]
            new_file = self.put_rows(merged)

            new_files = []
            for f in head["files"]:
                if f == selected[1]:
                    continue  # folded into the replacement object
                if f == selected[0]:
                    new_files.append(new_file)
                else:
                    new_files.append(f)
            assert len(new_files) == len(head["files"]) - 1

            # Crash point 1: new object is durable, but no metadata change has
            # been committed.  Process death leaves the open txn to SQLite's
            # journal rollback, so no half-published snapshot ever exists.
            if crash == "after-data":
                self._crash("after-data")

            version = self.publish(new_files, head["deleted"])
            result = {"status": "committed", "version": version,
                      "plan": name, "file": new_file}
            # Manifest and plan outcome commit atomically: no reader can see
            # the new snapshot while the plan still looks in-flight.
            self._finish_plan(name, result)
            txn.commit()

        # Crash point 2: publish and outcome are durable; only the caller's
        # response is lost.  Re-invocation returns the stored outcome.
        if crash == "after-publish":
            self._crash("after-publish")
        return result

    def _finish_plan(self, name, result):
        self.db.execute(
            "UPDATE plans SET outcome=? WHERE name=? AND outcome IS NULL",
            (encode(result), name))

    # --------------------------------------------------------- pins and GC
    def pin(self, version, remove=False):
        with self._txn():
            self.snapshot(version)  # reject unknown versions
            if remove:
                self.db.execute("DELETE FROM pins WHERE version=?", (version,))
            else:
                self.db.execute("INSERT OR IGNORE INTO pins VALUES (?)",
                                (version,))
        return {"status": "unpinned" if remove else "pinned",
                "version": version}

    def state(self):
        return {
            "head": self.snapshot()["version"],
            "retained": [v for v, in self.db.execute(
                "SELECT version FROM snapshots ORDER BY version")],
            "pins": [v for v, in self.db.execute(
                "SELECT version FROM pins ORDER BY version")],
            "objects": sorted(p.stem for p in self.objects.glob("*.json")),
            "plans": [
                {"name": n, "base": b, "files": json.loads(f),
                 "outcome": json.loads(o) if o else None}
                for n, b, f, o in self.db.execute(
                    "SELECT name,base,files,outcome FROM plans ORDER BY name")],
        }

    def gc(self, crash=None):
        # GC takes the same global write lock: mutually exclusive with all
        # other writers, so no manifest can change while references are checked.
        with self._txn() as txn:
            head = self.snapshot()["version"]
            retained = {head}
            retained.update(v for v, in self.db.execute(
                "SELECT version FROM pins"))
            # Baselines of plans that may still commit keep their snapshot.
            retained.update(b for b, outcome in self.db.execute(
                "SELECT base,outcome FROM plans") if outcome is None)

            referenced = set()
            live_versions = [v for v, in self.db.execute(
                "SELECT version FROM snapshots ORDER BY version")]
            for version in live_versions:
                if version in retained:
                    referenced.update(self.snapshot(version)["files"])

            # Version 0 (the empty seed snapshot) is retained only while some
            # retained reader actually needs it; otherwise it is prunable too.
            pruned = [v for v in live_versions if v not in retained]
            if pruned:
                self.db.executemany(
                    "DELETE FROM snapshots WHERE version=?",
                    [(v,) for v in pruned])

            on_disk = sorted(self.objects.glob("*.json"))
            unreferenced = [p.stem for p in on_disk
                            if p.stem not in referenced]
            staged = sorted(p.name for p in
                            self.objects.glob("*" + TMP_SUFFIX))

            if crash == "before-unlink":
                # Nothing destructive has happened; a plain rerun performs
                # the full collection.
                self._crash("before-unlink")

            removed = []
            for path in on_disk:
                if path.stem not in referenced:
                    path.unlink()
                    removed.append(path.stem)
            for path in sorted(self.objects.glob("*" + TMP_SUFFIX)):
                path.unlink()
                removed.append(path.name)
            # Metadata pruning is made durable before the call returns; the
            # file unlinks above already happened under the same write lock.
            txn.commit()
        return {"status": "collected", "retained": sorted(retained),
                "pruned_snapshots": pruned, "removed": removed,
                "unreferenced_before": unreferenced,
                "staged_before": staged}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("state")
    for action in ("append", "delete"):
        sub.add_parser(action).add_argument("input", type=Path)
    for action in ("pin", "unpin"):
        sub.add_parser(action).add_argument("version", type=int)
    sub.add_parser("read").add_argument("--version", type=int)
    sub.add_parser("prepare").add_argument("name")
    commit = sub.add_parser("commit")
    commit.add_argument("name")
    commit.add_argument("--crash", choices=["after-data", "after-publish"])
    sub.add_parser("gc").add_argument("--crash", choices=["before-unlink"])
    args = parser.parse_args()

    lake = Lake(args.root)
    try:
        if args.action in ("append", "delete"):
            payload = json.loads(args.input.read_text())
            result = getattr(lake, args.action)(payload)
        elif args.action in ("pin", "unpin"):
            result = lake.pin(args.version, args.action == "unpin")
        elif args.action == "read":
            result = lake.read(args.version)
        elif args.action == "prepare":
            result = lake.prepare(args.name)
        elif args.action == "commit":
            result = lake.commit(args.name, args.crash)
        elif args.action == "gc":
            result = lake.gc(args.crash)
        else:
            result = lake.state()
        print(encode(result))
    except (ValueError, KeyError, FileNotFoundError) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    main()
