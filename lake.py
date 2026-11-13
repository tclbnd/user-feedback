"""Manifest compaction lake with content-addressed data and SQLite metadata.

Delete semantics used by compaction
-----------------------------------
A snapshot manifest is ``version -> {files: [object], deleted: [row-id]}``.
A row is visible in a snapshot iff its id occurs in one of the snapshot's
objects and is *not* in ``deleted``.  ``deleted`` is therefore a snapshot-scoped
tombstone set, not a property of an object.

Compaction reads the two selected objects through a fixed base snapshot.  Rows
whose ids are tombstoned at commit time are omitted from the rewritten object
and their ids are dropped from the *new* manifest's tombstone set (the row no
longer exists in any object reachable from that manifest).  Tombstone ids that
refer to unselected or appended objects stay in the manifest.  The new manifest
therefore describes exactly the same live rows as the serial row-level model,
and old manifests keep describing the old live rows for pinned readers.

Concurrency
-----------
Every manifest writer (append, delete, commit, gc) takes SQLite's
``BEGIN IMMEDIATE`` write lock.  Data objects are immutable and written
atomically (temp file + fsync + rename); an object only becomes visible to
readers once a later manifest references it.  A commit never exposes a
half-written object, and gc cannot unlink an object between a commit's data
write and its manifest publication.
"""
import argparse
import hashlib
import json
import os
import secrets
import sqlite3
from pathlib import Path

CRASH_EXIT = 97


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def id_sorted(values):
    """Deterministic ordering for homogeneous/scalar row ids."""
    try:
        return sorted(values)
    except TypeError:
        return sorted(values, key=encode)


def row_key(row):
    try:
        return row["id"]
    except TypeError:
        return encode(row["id"])


class Lake:
    def __init__(self, root):
        self.root = root
        self.objects = root / "objects"
        self.objects.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(root / "catalog.db", timeout=30)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots (
                version INTEGER PRIMARY KEY, files TEXT NOT NULL, deleted TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS current (id INTEGER PRIMARY KEY, version INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS pins (version INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS plans (
                name TEXT PRIMARY KEY, base INTEGER NOT NULL, files TEXT NOT NULL,
                outcome TEXT
            );
            CREATE TABLE IF NOT EXISTS conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT, version INTEGER, reason TEXT, detail TEXT
            );
        """)
        # Seed the empty root snapshot only for a brand-new catalog.  Seeding
        # unconditionally would resurrect version 0 after gc prunes it.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0:
                self.db.execute("INSERT INTO snapshots VALUES (0, '[]', '[]')")
            self.db.execute(
                "INSERT OR IGNORE INTO current VALUES (0, ?)",
                (self.db.execute(
                    "SELECT COALESCE(MAX(version),0) FROM snapshots").fetchone()[0],))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    # --------------------------------------------------------------- objects
    def object_path(self, name):
        return self.objects / (name + ".json")

    def _atomic_write(self, path, payload):
        """Write an object without ever exposing a partially written file."""
        token = secrets.token_hex(8)
        tmp = path.with_name(".%s.%d.%s.tmp" % (path.name, os.getpid(), token))
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            try:  # best effort: make the rename durable on POSIX too
                directory = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError:
                pass
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    def get_rows(self, name):
        path = self.object_path(name)
        try:
            payload = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise ValueError("missing object %s" % name)
        if hashlib.sha256(payload.encode()).hexdigest() != name:
            raise ValueError("corrupt object %s" % name)
        rows = json.loads(payload)
        if not isinstance(rows, list):
            raise ValueError("object %s does not contain a row list" % name)
        for row in rows:
            if not isinstance(row, dict) or "id" not in row:
                raise ValueError("object %s contains a row without an id" % name)
        return rows

    def put_rows(self, rows):
        payload = encode(rows)
        name = hashlib.sha256(payload.encode()).hexdigest()
        path = self.object_path(name)
        if not path.exists() or path.read_text(encoding="utf-8") != payload:
            self._atomic_write(path, payload)
        return name

    # ------------------------------------------------------------- manifests
    def snapshot(self, version=None):
        if version is None:
            row = self.db.execute("SELECT version FROM current WHERE id=0").fetchone()
            version = row[0]
        row = self.db.execute(
            "SELECT files,deleted FROM snapshots WHERE version=?", (version,)).fetchone()
        if row is None:
            raise ValueError("unknown snapshot %s" % version)
        return {"version": version, "files": json.loads(row[0]),
                "deleted": json.loads(row[1])}

    def publish(self, files, deleted):
        """Insert the next manifest and move head. Runs inside a transaction."""
        version = self.snapshot()["version"] + 1
        self.db.execute("INSERT INTO snapshots VALUES (?,?,?)",
                        (version, encode(files), encode(id_sorted(deleted))))
        self.db.execute("UPDATE current SET version=? WHERE id=0", (version,))
        return version

    def read(self, version=None):
        snap = self.snapshot(version)
        deleted = set(snap["deleted"])
        rows = [row for name in snap["files"] for row in self.get_rows(name)
                if row["id"] not in deleted]
        return {"rows": sorted(rows, key=row_key), **snap}

    # ------------------------------------------------------------ append/delete
    def append(self, rows):
        if not isinstance(rows, list):
            raise ValueError("append input must be a list of rows")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            snap = self.snapshot()
            name = self.put_rows(rows)
            version = self.publish([*snap["files"], name], snap["deleted"])
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return {"version": version}

    def delete(self, ids):
        if not isinstance(ids, list):
            raise ValueError("delete input must be a list of row ids")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            snap = self.snapshot()
            version = self.publish(snap["files"], set(snap["deleted"]).union(ids))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return {"version": version}

    # -------------------------------------------------------------- compaction
    def prepare(self, name):
        """Fix the base version and first two objects; publish nothing."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            snap = self.snapshot()
            files = snap["files"][:2]
            if len(files) < 2:
                raise ValueError("at least two files required")
            existing = self.db.execute(
                "SELECT base,files FROM plans WHERE name=?", (name,)).fetchone()
            if existing is not None:
                self.db.commit()
                return {"base": existing[0], "files": json.loads(existing[1])}
            self.db.execute("INSERT INTO plans VALUES (?,?,?,NULL)",
                            (name, snap["version"], encode(files)))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return {"base": snap["version"], "files": files}

    def _conflict(self, name, current_version, reason, detail=None):
        """Close the plan as a conflict without advancing the version."""
        result = {"status": "conflict", "version": current_version, "reason": reason}
        self.db.execute("UPDATE plans SET outcome=? WHERE name=?",
                        (encode(result), name))
        self.db.execute(
            "INSERT INTO conflicts (name,version,reason,detail) VALUES (?,?,?,?)",
            (name, current_version, reason, encode(detail or {})))
        return result

    def commit(self, name, crash=None):
        """Publish one merged object replacing the plan's two selected objects.

        The plan is first validated against the *current* head.  Appends made
        after the base are rebased (their objects are kept); deletes that only
        touch unselected rows are rebased too.  Replaced selected objects or
        deletes touching selected rows close the plan as a conflict, without
        advancing the version.  A successful plan can never be re-committed.
        """
        self.db.execute("BEGIN IMMEDIATE")
        try:
            record = self.db.execute(
                "SELECT base,files,outcome FROM plans WHERE name=?", (name,)).fetchone()
            if record is None:
                raise KeyError("unknown plan %s" % name)
            base = record[0]
            selected = json.loads(record[1])
            if record[2] is not None:
                result = json.loads(record[2])
                self.db.commit()
                return result

            try:
                current = self.snapshot()
                base_snap = self.snapshot(base)
                loaded = [(name_, self.get_rows(name_)) for name_ in selected]
            except ValueError as error:
                result = self._conflict(
                    name, self.snapshot()["version"], "unreadable-input",
                    {"error": str(error), "files": selected})
                self.db.commit()
                return result

            # 1. Selected inputs must still be reachable from the current head.
            #    A file removed by another compaction means its rows were
            #    already rewritten into the head; replacing it again would
            #    duplicate ids or hide appends.
            missing = [name_ for name_ in selected if name_ not in current["files"]]
            if missing:
                result = self._conflict(
                    name, current["version"], "selected-file-replaced",
                    {"missing": missing, "files": selected})
                self.db.commit()
                return result

            selected_ids = set()
            for _, rows in loaded:
                selected_ids.update(row["id"] for row in rows)

            # 2. New tombstones since the base must not touch selected rows:
            #    the rewrite would then disagree with the serial model about
            #    which rows are live.
            base_deleted = set(base_snap["deleted"])
            current_deleted = set(current["deleted"])
            new_deletes = current_deleted - base_deleted
            affected = new_deletes & selected_ids
            if affected:
                result = self._conflict(
                    name, current["version"], "delete-affects-selection",
                    {"ids": id_sorted(affected)})
                self.db.commit()
                return result

            # 3. Rewrite: ids and data are preserved, tombstoned rows are
            #    physically omitted and their tombstones leave the manifest.
            merged = []
            dropped = set()
            for _, rows in loaded:
                for row in rows:
                    if row["id"] in current_deleted:
                        dropped.add(row["id"])
                    else:
                        merged.append(row)
            new_name = self.put_rows(merged)

            if crash == "after-data":
                # Object is durably on disk; no manifest and no plan outcome
                # were recorded.  Rerunning commit reuses the same object.
                os._exit(CRASH_EXIT)

            # 4. Rebase into the current file order so appends that landed
            #    after the base survive; the merged object takes the slot of
            #    the first selected file.
            new_files = []
            placed = False
            for name_ in current["files"]:
                if name_ in selected:
                    if not placed:
                        new_files.append(new_name)
                        placed = True
                else:
                    new_files.append(name_)
            if not placed:  # defensive; validation above makes this impossible
                new_files.append(new_name)
            new_deleted = current_deleted - dropped

            version = self.publish(new_files, new_deleted)
            result = {"status": "committed", "version": version}
            self.db.execute("UPDATE plans SET outcome=? WHERE name=?",
                            (encode(result), name))
            self.db.commit()
            if crash == "after-publish":
                # Manifest and plan outcome are durable; the caller sees nothing.
                os._exit(CRASH_EXIT)
            return result
        except BaseException:
            try:
                self.db.rollback()
            except sqlite3.Error:
                pass
            raise

    # ---------------------------------------------------------- pins and gc
    def pin(self, version, remove=False):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.snapshot(version)
            if remove:
                self.db.execute("DELETE FROM pins WHERE version=?", (version,))
            else:
                self.db.execute("INSERT OR IGNORE INTO pins VALUES (?)", (version,))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return {"version": version}

    def state(self):
        head = self.snapshot()
        # ``retained`` stays "manifests still stored" (the pre-existing
        # contract); ``protected`` is the set gc must keep and ``collectable``
        # is what a gc would drop.
        retained = [v for v, in self.db.execute(
            "SELECT version FROM snapshots ORDER BY version")]
        protected = sorted(self._retained_versions())
        pins = [v for v, in self.db.execute(
            "SELECT version FROM pins ORDER BY version")]
        bases = [b for b, in self.db.execute(
            "SELECT base FROM plans WHERE outcome IS NULL ORDER BY base")]
        objects = sorted(path.stem for path in self.objects.glob("*.json"))
        conflicts = self.db.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0]
        return {"head": head["version"], "retained": retained,
                "protected": protected,
                "collectable": [v for v in retained if v not in protected],
                "pins": pins, "objects": objects,
                "pending_bases": sorted(set(bases)), "conflicts": conflicts}

    def _retained_versions(self):
        current = self.snapshot()["version"]
        pins = {v for v, in self.db.execute("SELECT version FROM pins")}
        bases = {b for b, in self.db.execute(
            "SELECT base FROM plans WHERE outcome IS NULL")}
        retained = {current} | pins | bases
        for version in retained:
            self.snapshot(version)  # every retained manifest must exist
        return retained

    def _unreferenced_objects(self, retained):
        referenced = set()
        for version in retained:
            referenced.update(self.snapshot(version)["files"])
        all_objects = {path.stem for path in self.objects.glob("*.json")}
        return sorted(all_objects - referenced)

    def gc(self, crash=None):
        """Drop non-retained manifests, then reclaim objects nothing references.

        Two locked phases keep every crash point consistent:

        1. Delete the manifests that nothing protects and commit.  Removing a
           manifest only removes references, so any crash leaves at worst
           unreferenced garbage on disk, never a manifest whose object is gone.
        2. Re-derive the unreferenced object set *inside* a fresh write lock,
           then unlink it.  Recomputing under the lock closes the race with a
           concurrent commit that writes a byte-identical object: it either
           published (so we see the reference and skip the file) or is still
           waiting for the lock (so it will re-create the file after we commit).
        """
        self.db.execute("BEGIN IMMEDIATE")
        try:
            retained = self._retained_versions()
            obsolete = [v for v, in self.db.execute("SELECT version FROM snapshots")
                        if v not in retained]
            unreferenced = self._unreferenced_objects(retained)
            if crash == "before-unlink" and (obsolete or unreferenced):
                # Nothing was mutated or unlinked yet; rerunning gc without
                # the flag is a complete recovery.
                os._exit(CRASH_EXIT)
            for version in obsolete:
                self.db.execute("DELETE FROM snapshots WHERE version=?", (version,))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

        self.db.execute("BEGIN IMMEDIATE")
        try:
            # Every stored manifest now belongs to a protected version.
            referenced = set()
            for version, in self.db.execute("SELECT version FROM snapshots"):
                referenced.update(self.snapshot(version)["files"])
            all_objects = {path.stem for path in self.objects.glob("*.json")}
            unreferenced = sorted(all_objects - referenced)
            removed = []
            for object_name in unreferenced:
                try:
                    self.object_path(object_name).unlink()
                except FileNotFoundError:
                    pass
                removed.append(object_name)
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return {"removed": removed, "retained": sorted(retained),
                "unreferenced": unreferenced}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("state")
    for action in ("append","delete"):
        sub.add_parser(action).add_argument("input", type=Path)
    for action in ("pin","unpin"):
        sub.add_parser(action).add_argument("version", type=int)
    sub.add_parser("read").add_argument("--version", type=int)
    sub.add_parser("prepare").add_argument("name")
    commit = sub.add_parser("commit")
    commit.add_argument("name")
    commit.add_argument("--crash", choices=["after-data","after-publish"])
    sub.add_parser("gc").add_argument("--crash", choices=["before-unlink"])
    args = parser.parse_args()
    lake = Lake(args.root)
    try:
        if args.action in ("append","delete"):
            result = getattr(lake,args.action)(json.loads(args.input.read_text()))
        elif args.action in ("pin","unpin"):
            result = lake.pin(args.version,args.action=="unpin")
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
    except (ValueError, KeyError) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    main()
