"""Replica store and transaction applier."""
import json
import os


def write_json(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class Replica:
    def __init__(self, directory, on_commit=None):
        self.dir, self.on_commit = directory, on_commit
        os.makedirs(directory, exist_ok=True)
        self.path = os.path.join(directory, "replica.json")
        self.conflict_path = os.path.join(directory, "conflicts.jsonl")
        state = {"tables": {}}
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as fh:
                state = json.load(fh)
        self.tables = {name: dict(t, rows={json.dumps(r[t["primary_key"]]): r for r in t["rows"]})
                       for name, t in state["tables"].items()}

    def log_conflict(self, entry):
        with open(self.conflict_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def commit(self):
        out = {name: {"primary_key": t["primary_key"], "columns": t["columns"],
                      "rows": [t["rows"][k] for k in sorted(t["rows"], key=lambda k: json.loads(k))]}
               for name, t in self.tables.items()}
        write_json(self.path, {"tables": out})
        if self.on_commit:
            self.on_commit()


class Applier:
    def __init__(self, replica):
        self.replica = replica
        self.xid, self.pending = None, []

    def receive(self, m):
        """Handle one message; returns the commit LSN once a transaction is applied."""
        if m["type"] == "begin":
            self.xid, self.pending = m["xid"], []
        elif m["type"] == "commit":
            for change in self.pending:
                self.apply(change, m["lsn"])
            self.replica.commit()
            self.xid, self.pending = None, []
            return m["lsn"]
        elif m["type"] == "ddl":
            ddl(self.replica.tables, m)
        else:
            self.xid = m["xid"]
            self.pending.append(m)
        return None

    def apply(self, m, commit_lsn):
        t = self.replica.tables[m["table"]]
        if m["type"] == "delete":
            t["rows"].pop(json.dumps(m["key"]), None)
            return
        row = {c: m["row"].get(c) for c in t["columns"]}
        old = json.dumps(m["row"][t["primary_key"]] if m["type"] == "insert" else m["key"])
        if m["type"] == "insert" and old in t["rows"]:
            self.replica.log_conflict({"commit_lsn": commit_lsn, "lsn": m["lsn"], "type": "insert_exists",
                                       "table": m["table"], "key": m["row"][t["primary_key"]]})
        t["rows"].pop(old, None)
        t["rows"][json.dumps(row[t["primary_key"]])] = row


def ddl(tables, m):
    if m["op"] == "create_table":
        tables[m["table"]] = {"primary_key": m["primary_key"], "columns": dict(m["columns"]), "rows": {}}
        return
    t = tables[m["table"]]
    if m["op"] == "add_column":
        t["columns"][m["column"]] = m.get("default")
        for row in t["rows"].values():
            row[m["column"]] = m.get("default")
    elif m["op"] == "drop_column":
        t["columns"].pop(m["column"], None)
        for row in t["rows"].values():
            row.pop(m["column"], None)
