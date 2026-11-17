"""Index consistency check and logged, resumable online repair."""
import json
import os


def check(table):
    problems, live = [], dict(table.rows())
    for ix in table.indexes:
        name, entries = ix["name"], table.entries[ix["name"]]
        for key, rid in entries:
            row = table.row(rid)
            if row is None:
                problems.append((name, "dangling", key, rid))
            elif table.value(row, ix) != key:
                problems.append((name, "mismatch", key, rid))
        keys = {key for key, _ in entries}
        for rid, row in sorted(live.items()):
            key = table.value(row, ix)
            if key not in keys:
                problems.append((name, "missing", key, rid))
    return [{"index": n, "kind": k, "key": key, "rowid": list(rid)} for n, k, key, rid in sorted(problems)]


ORDER = {"quarantine": 0, "drop": 1, "add": 2}


def plan(problems):
    ops = {"duplicate": "quarantine", "dangling": "drop", "mismatch": "drop", "missing": "add"}
    actions = sorted(({"op": ops[p["kind"]], "index": p["index"], "key": p["key"], "rowid": p["rowid"]}
                      for p in problems), key=lambda a: (ORDER[a["op"]], a["index"], a["key"], a["rowid"]))
    return [{"seq": i + 1, **a} for i, a in enumerate(actions)]


def apply(table, a):
    if a["op"] == "drop":
        table.drop_entry(a["index"], a["key"], a["rowid"])
    elif a["op"] == "add":
        table.add_entry(a["index"], a["key"], a["rowid"])
    else:
        with open(table.path("quarantine.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"rowid": a["rowid"], "row": table.row(a["rowid"])}) + "\n")
        table.delete(a["rowid"])


class Crash(Exception):
    pass


def repair(table, writes=(), crash_after=None):
    log_path = table.path("repair.log")
    lines = []
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh if line.strip()]
    if not lines or lines[-1].get("complete"):
        lines = plan(check(table))
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(a) + "\n" for a in lines)
    actions = [a for a in lines if "op" in a]
    done = {line["done"] for line in lines if "done" in line}
    pending = sorted(writes, key=lambda w: w.get("before_action", 0))
    summary = {"plan": len(actions), "applied": 0, "skipped": 0}
    for a in actions:
        if a["seq"] in done:
            continue
        while pending and pending[0].get("before_action", 0) <= a["seq"]:
            concurrent(table, pending.pop(0))
        apply(table, a)
        summary["applied"] += 1
        table.save()
        if crash_after is not None and summary["applied"] + summary["skipped"] >= crash_after:
            raise Crash()
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"done": a["seq"], "applied": True}) + "\n")
    for w in pending:
        concurrent(table, w)
    table.save()
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"complete": True}) + "\n")
    return summary


def concurrent(table, w):
    if w["op"] == "insert":
        table.insert(w["row"])
    elif w["op"] == "delete":
        table.delete(w["rowid"])
    else:
        table.update(w["rowid"], w["row"])
