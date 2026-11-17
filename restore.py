"""Point-in-time restore: newest usable base backup plus the archived log in commit order."""
import json
import os


class RestoreError(Exception):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = {"error": message, **details}


def load_backups(cluster):
    root = os.path.join(cluster, "backups")
    out = []
    for name in sorted(os.listdir(root)):
        with open(os.path.join(root, name, "manifest.json"), encoding="utf-8") as fh:
            out.append({**json.load(fh), "path": os.path.join(root, name)})
    return out


def cut_point(records, target):
    """Commits with lsn <= the returned cut belong to the restore."""
    kind, value = target
    if kind == "time":
        return max([l for l, r in records.items() if r["time"] < value], default=0)
    commit = [l for l, r in records.items() if r["type"] == "COMMIT" and r["tx"] == value]
    if not commit:
        raise RestoreError("transaction not found", tx=value)
    return commit[0] if kind == "tx" else commit[0] - 1


def choose_backup(backups, target, cut):
    kind, value = target
    usable = [b for b in backups if (b["start_time"] <= value if kind == "time" else b["start_lsn"] <= cut)]
    if not usable:
        raise RestoreError("no usable backup")
    return max(usable, key=lambda b: b["start_lsn"])


def replay(tables, records, backup, cut):
    writes, committed = {}, set()
    for lsn in sorted(records):
        rec = records[lsn]
        if lsn <= backup["end_lsn"] or lsn > cut:
            continue
        if rec["type"] in ("PUT", "DEL"):
            writes.setdefault(rec["tx"], []).append(rec)
        elif rec["type"] == "COMMIT":
            committed.add(rec["tx"])
    for tx, recs in writes.items():
        if tx not in committed:
            continue
        for w in recs:
            rows = tables.setdefault(w["table"], {})
            if w["type"] == "PUT":
                rows[w["key"]] = w["value"]
            else:
                rows.pop(w["key"], None)
    return {name: rows for name, rows in sorted(tables.items()) if rows}


def install(out, tables):
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "data.json"), "w", encoding="utf-8") as fh:
        json.dump({"tables": tables}, fh, indent=1, sort_keys=True)


def restore(cluster, out, target, records):
    cut = cut_point(records, target)
    backup = choose_backup(load_backups(cluster), target, cut)
    with open(os.path.join(backup["path"], "data.json"), encoding="utf-8") as fh:
        tables = json.load(fh)["tables"]
    tables = replay(tables, records, backup, cut)
    install(out, tables)
    return {"backup": os.path.basename(backup["path"]), "cut_lsn": cut}
