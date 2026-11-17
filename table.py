"""JSON table store with change-capture triggers, table locks and a logical clock."""
import json
import os


class Database:
    def __init__(self, path, workload=()):
        self.path = path
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.clock = data.get("clock", 0)
        self.triggers = data.get("triggers", [])
        self.tables = {}
        for name, t in data["tables"].items():
            self.create(name, t["pk"], t["columns"], t.get("defaults"))
            for row in t["rows"]:
                self.put(name, row)
        self.pending = sorted((w for w in workload if w["tick"] > self.clock), key=lambda w: w["tick"])
        self.locks, self.queued, self.lock_ticks = {}, [], 0

    def save(self):
        tables = {n: {**t, "rows": [t["rows"][k] for k in sorted(t["rows"])]} for n, t in self.tables.items()}
        with open(self.path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump({"clock": self.clock, "triggers": self.triggers, "tables": tables}, fh, indent=1, sort_keys=True)
        os.replace(self.path + ".tmp", self.path)

    def create(self, name, pk, columns, defaults=None):
        self.tables[name] = {"pk": pk, "columns": list(columns), "defaults": dict(defaults or {}), "rows": {}}

    def put(self, name, values):
        t = self.tables[name]
        row = {c: values[c] if c in values else t["defaults"].get(c) for c in t["columns"]}
        t["rows"][row[t["pk"]]] = row
        return row

    def chunk(self, name, offset, size):
        rows = self.tables[name]["rows"]
        keys = sorted(rows)[offset:offset + size]
        return [dict(rows[k]) for k in keys]

    def rename(self, old, new):
        if new in self.tables:
            raise ValueError(f"table {new} already exists")
        self.tables[new] = self.tables.pop(old)
        for trig in self.triggers:
            if trig["table"] == old:
                trig["table"] = new

    def apply(self, w):
        name = w["table"]
        if name not in self.tables:
            return
        t = self.tables[name]
        rows, pk = t["rows"], t["pk"]
        if w["op"] == "insert":
            if w["row"].get(pk) in rows:
                return
            row = self.put(name, w["row"])
            change = {"op": "insert", "pk": row[pk], "row": row}
        elif w["op"] == "update":
            if w["pk"] not in rows:
                return
            row = {**rows[w["pk"]], **{c: v for c, v in w["set"].items() if c in t["columns"]}}
            if row[pk] != w["pk"] and row[pk] in rows:
                return
            del rows[w["pk"]]
            rows[row[pk]] = row
            change = {"op": "update", "pk": row[pk], "old_pk": w["pk"], "row": dict(row)}
        else:
            if rows.pop(w["pk"], None) is None:
                return
            change = {"op": "delete", "pk": w["pk"]}
        for trig in self.triggers:
            if trig["table"] == name:
                log = self.tables[trig["log"]]["rows"]
                log[len(log) + 1] = {"seq": len(log) + 1, "old_pk": None, "row": None, **change}

    def tick(self):
        self.clock += 1
        while self.pending and self.pending[0]["tick"] <= self.clock:
            w = self.pending.pop(0)
            if w["table"] in self.locks:
                self.queued.append(w)
            else:
                self.apply(w)

    def lock(self, name):
        self.locks[name] = self.clock

    def unlock(self, name):
        self.lock_ticks = max(self.lock_ticks, self.clock - self.locks.pop(name))
        waiting = [w for w in self.queued if w["table"] == name]
        self.queued = [w for w in self.queued if w["table"] != name]
        for w in waiting:
            self.apply(w)

    def finish(self):
        while self.pending:
            self.tick()
