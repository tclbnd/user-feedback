"""Index consistency check and logged, resumable online repair."""
import json
import os

from heap import LIVE

END = float("inf")  # writes without "before_action" run after the last action


def live_row(table, rid):
    """The row at rid, or None unless the slot is live.

    A deleted slot keeps its old bytes until it is reused, so Table.row()
    alone cannot tell a live row from a stale one; the slot flag can.
    """
    got = table.slot(tuple(rid))
    if got is None or got[0] != LIVE:
        return None
    return json.loads(got[1])


def check(table):
    problems, live = [], dict(table.rows())
    for ix in table.indexes:
        name, entries = ix["name"], table.entries[ix["name"]]
        have = set(entries)
        for key, rid in entries:
            if rid not in live:
                problems.append((name, "dangling", key, rid))
            elif table.value(live[rid], ix) != key:
                problems.append((name, "mismatch", key, rid))
        for rid, row in sorted(live.items()):
            key = table.value(row, ix)
            if (key, rid) not in have:
                problems.append((name, "missing", key, rid))
        if ix.get("unique"):
            holders = {}
            for rid, row in sorted(live.items()):
                holders.setdefault(table.value(row, ix), []).append(rid)
            for key, rids in holders.items():
                for rid in rids[1:]:
                    problems.append((name, "duplicate", key, rid))
    return [{"index": n, "kind": k, "key": key, "rowid": list(rid)} for n, k, key, rid in sorted(problems)]


ORDER = {"quarantine": 0, "drop": 1, "add": 2}


def plan(problems):
    ops = {"duplicate": "quarantine", "dangling": "drop", "mismatch": "drop", "missing": "add"}
    actions = sorted(({"op": ops[p["kind"]], "index": p["index"], "key": p["key"], "rowid": p["rowid"]}
                      for p in problems), key=lambda a: (ORDER[a["op"]], a["index"], a["key"], a["rowid"]))
    return [{"seq": i + 1, **a} for i, a in enumerate(actions)]


def needed(table, a):
    """Re-check a planned action against the current files.

    Concurrent writes (and quarantine deletes) can make an action unnecessary
    or outright wrong by the time it runs, so it is skipped unless the problem
    it fixes still exists.
    """
    ix = next(i for i in table.indexes if i["name"] == a["index"])
    rid = tuple(a["rowid"])
    row = live_row(table, rid)
    if a["op"] == "drop":
        return (a["key"], rid) in table.entries[a["index"]] and (
            row is None or table.value(row, ix) != a["key"])
    if a["op"] == "add":
        return (row is not None and table.value(row, ix) == a["key"]
                and (a["key"], rid) not in table.entries[a["index"]])
    if row is None:  # quarantine: the row is already gone
        return False
    return any(other < rid and table.value(orow, ix) == table.value(row, ix)
               for other, orow in table.rows())


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
    resumed = bool(lines) and not lines[-1].get("complete")
    if not resumed:
        lines = plan(check(table))
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(a) + "\n" for a in lines)
    actions = [a for a in lines if "op" in a]
    done = {line["done"] for line in lines if "done" in line}
    pending = sorted(writes, key=lambda w: w.get("before_action", END))
    if resumed:
        # The crashed run was saved after the action following the last logged
        # one, so every write scheduled up to that action is already on disk;
        # applying it again would insert duplicates.
        threshold = (max(done) if done else 0) + 1
        pending = [w for w in pending if w.get("before_action", END) > threshold]
    summary = {"plan": len(actions), "applied": 0, "skipped": 0}
    processed = 0
    for a in actions:
        while pending and pending[0].get("before_action", END) <= a["seq"]:
            concurrent(table, pending.pop(0))
        if a["seq"] in done:
            continue
        if needed(table, a):
            apply(table, a)
            applied = True
            summary["applied"] += 1
        else:
            applied = False
            summary["skipped"] += 1
        table.save()
        processed += 1
        if crash_after is not None and processed >= crash_after:
            raise Crash()
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"done": a["seq"], "applied": applied}) + "\n")
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
