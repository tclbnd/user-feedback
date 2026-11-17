"""Shadow-table migration: chunked copy, change replay, short-lock cut-over, rollback."""

PHASES = ("copy", "replay", "cutover", "swap")


class Failure(Exception):
    pass


class Migration:
    def __init__(self, db, alter, chunk=4, fail_at=None):
        self.db, self.alter, self.chunk, self.fail_at = db, alter, chunk, fail_at
        name = self.table = alter["table"]
        self.shadow, self.log = f"_osc_new_{name}", f"_osc_log_{name}"
        self.trigger, self.old = f"_osc_trg_{name}", f"_osc_old_{name}"
        self.summary = {"table": name, "copied": 0, "replayed": 0}

    def inject(self, phase):
        if self.fail_at == phase:
            raise Failure(f"injected failure during {phase}")

    def target_schema(self):
        src = self.db.tables[self.table]
        drop = set(self.alter.get("drop", [])) - {src["pk"]}
        columns = [c for c in src["columns"] if c not in drop]
        defaults = {c: v for c, v in src["defaults"].items() if c not in drop}
        for col in self.alter.get("add", []):
            columns.append(col["name"])
            defaults[col["name"]] = col.get("default")
        return src["pk"], columns, defaults

    def replay(self, entries):
        upserts, deletes = {}, set()
        for e in entries:
            if e["op"] == "delete":
                upserts.pop(e["pk"], None)
                deletes.add(e["pk"])
            else:
                upserts[e["pk"]] = e["row"]
        for row in upserts.values():
            self.db.put(self.shadow, row)
        for key in deletes:
            self.db.tables[self.shadow]["rows"].pop(key, None)
        self.summary["replayed"] += len(entries)
        return entries[-1]["seq"] if entries else None

    def backlog(self, position):
        log = self.db.tables[self.log]["rows"]
        return [log[s] for s in sorted(log) if s > position]

    def run(self):
        db = self.db
        db.create(self.shadow, *self.target_schema())
        db.create(self.log, "seq", ["seq", "op", "pk", "old_pk", "row"])
        db.triggers.append({"name": self.trigger, "table": self.table, "log": self.log})

        offset = 0
        while True:
            rows = db.chunk(self.table, offset, self.chunk)
            if not rows:
                break
            for row in rows:
                db.put(self.shadow, row)
            self.summary["copied"] += len(rows)
            offset += len(rows)
            self.inject("copy")
            db.tick()

        db.lock(self.table)
        self.inject("cutover")
        position = 0
        while self.backlog(position):
            position = self.replay(self.backlog(position)[:self.chunk])
            self.inject("replay")
            db.tick()
        db.rename(self.table, self.old)
        self.inject("swap")
        db.rename(self.shadow, self.table)
        self.cleanup()
        db.unlock(self.table)
        db.tables.pop(self.old, None)

    def cleanup(self):
        self.db.triggers[:] = [t for t in self.db.triggers if t["name"] != self.trigger]
        self.db.tables.pop(self.log, None)
        self.db.tables.pop(self.shadow, None)

    def rollback(self):
        if self.table in self.db.locks:
            self.db.unlock(self.table)
        self.db.tables.pop(self.shadow, None)
