"""Local manifest table with content-addressed data and SQLite metadata."""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class Lake:
    def __init__(self, root):
        self.root = root
        self.objects = root / "objects"
        self.objects.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(root / "catalog.db", timeout=10)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots (
                version INTEGER PRIMARY KEY, files TEXT NOT NULL, deleted TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS current (id INTEGER PRIMARY KEY, version INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS pins (version INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS plans (
                name TEXT PRIMARY KEY, base INTEGER NOT NULL, files TEXT NOT NULL, outcome TEXT
            );
            INSERT OR IGNORE INTO snapshots VALUES (0, '[]', '[]');
            INSERT OR IGNORE INTO current VALUES (0, 0);
        """)

    def snapshot(self, version=None):
        if version is None:
            version = self.db.execute("SELECT version FROM current WHERE id=0").fetchone()[0]
        row = self.db.execute("SELECT files,deleted FROM snapshots WHERE version=?", (version,)).fetchone()
        if row is None:
            raise ValueError("unknown snapshot")
        return {"version":version,"files":json.loads(row[0]),"deleted":json.loads(row[1])}

    def get_rows(self, name):
        return json.loads((self.objects/(name+".json")).read_text())

    def put_rows(self, rows):
        payload = encode(rows)
        name = hashlib.sha256(payload.encode()).hexdigest()
        (self.objects/(name+".json")).write_text(payload)
        return name

    def publish(self, files, deleted):
        version = self.snapshot()["version"] + 1
        self.db.execute("INSERT INTO snapshots VALUES (?,?,?)", (version,encode(files),encode(sorted(deleted))))
        self.db.execute("UPDATE current SET version=? WHERE id=0", (version,))
        return version

    def append(self, rows):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            snap = self.snapshot()
            version = self.publish([*snap["files"], self.put_rows(rows)], snap["deleted"])
        return {"version":version}

    def delete(self, ids):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            snap = self.snapshot()
            version = self.publish(snap["files"], set(snap["deleted"]).union(ids))
        return {"version":version}

    def read(self, version):
        snap = self.snapshot(version)
        deleted = set(snap["deleted"])
        rows = [row for name in snap["files"] for row in self.get_rows(name) if row["id"] not in deleted]
        return {"rows":sorted(rows, key=lambda row:row["id"]), **snap}

    def prepare(self, name):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            snap = self.snapshot()
            files = snap["files"][:2]
            if len(files) < 2:
                raise ValueError("at least two files required")
            self.db.execute("INSERT OR IGNORE INTO plans VALUES (?,?,?,NULL)", (name,snap["version"],encode(files)))
            base, selected = self.db.execute("SELECT base,files FROM plans WHERE name=?", (name,)).fetchone()
        return {"base":base,"files":json.loads(selected)}

    def commit(self, name, crash):
        base, selected = self.db.execute("SELECT base,files FROM plans WHERE name=?", (name,)).fetchone()
        snap = self.snapshot(base)
        selected = json.loads(selected)
        rows = [row for f in selected for row in self.get_rows(f) if row["id"] not in snap["deleted"]]
        # Known race: replacing against the old manifest silently loses appends
        # or removes newer delete markers. There is no saved commit outcome.
        with self.db:
            new = self.put_rows(rows)
            version = self.publish([f for f in snap["files"] if f not in selected] + [new], snap["deleted"])
        return {"status":"committed","version":version}

    def pin(self, version, remove=False):
        with self.db:
            self.snapshot(version)
            if remove:
                self.db.execute("DELETE FROM pins WHERE version=?", (version,))
            else:
                self.db.execute("INSERT OR IGNORE INTO pins VALUES (?)", (version,))
        return {"version":version}

    def state(self):
        return {"head":self.snapshot()["version"],
                "retained":[v for v, in self.db.execute("SELECT version FROM snapshots ORDER BY version")],
                "pins":[v for v, in self.db.execute("SELECT version FROM pins ORDER BY version")],
                "objects":sorted(path.stem for path in self.objects.glob("*.json"))}

    def gc(self, crash):
        # The baseline only protects the current version.
        files = set(self.snapshot()["files"])
        removed = []
        for path in sorted(self.objects.glob("*.json")):
            if path.stem not in files:
                removed.append(path.stem)
                path.unlink()
        return {"removed":removed}


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
            result = lake.commit(args.name,args.crash)
        elif args.action == "gc":
            result = lake.gc(args.crash)
        else:
            result = lake.state()
        print(encode(result))
    except (ValueError, KeyError) as error:
        parser.exit(2,str(error)+"\n")


if __name__ == "__main__":
    main()
