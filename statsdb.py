#!/usr/bin/env python3
"""Replay table changes and planner estimates: statsdb.py run|report <workload.json>."""
import json
import operator
import sys

from selectivity import access_path, estimate
from stats import analyze

STALE_BASE, STALE_SCALE = 50, 0.1
OPS = {"=": operator.eq, "!=": operator.ne, "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


def matches(row, expr):
    if "and" in expr:
        return all(matches(row, e) for e in expr["and"])
    if "or" in expr:
        return any(matches(row, e) for e in expr["or"])
    v, op = row[expr["col"]], expr["op"]
    if op in ("is_null", "not_null"):
        return (v is None) == (op == "is_null")
    if v is None:
        return False
    return v in expr["values"] if op == "in" else OPS[op](v, expr["value"])


class Table:
    def __init__(self, spec):
        self.columns = {c: list(v) for c, v in spec["columns"].items()}
        self.indexes = spec.get("indexes", [])
        self.stats, self.changed = None, 0

    def rows(self):
        names = list(self.columns)
        return [dict(zip(names, vals)) for vals in zip(*self.columns.values())]

    def stale(self):
        return self.stats is None or self.changed > STALE_BASE + STALE_SCALE * self.stats["rows"]

    def apply(self, ev):
        op = ev["op"]
        if op == "analyze":
            self.stats, self.changed = analyze({"columns": self.columns}), 0
            return {"stats": self.stats}
        if op == "insert":
            added = len(next(iter(ev["columns"].values())))
            for c in self.columns:
                self.columns[c] += ev["columns"].get(c, [None] * added)
            self.changed += added
            return {"inserted": added}
        if op == "delete":
            keep = [r for r in self.rows() if not matches(r, ev["where"])]
            removed = len(self.columns[next(iter(self.columns))]) - len(keep)
            self.columns = {c: [r[c] for r in keep] for c in self.columns}
            return {"deleted": removed}
        if op == "estimate":
            refreshed = self.stale()
            if refreshed:
                self.stats, self.changed = analyze({"columns": self.columns}), 0
            sel = estimate(self.stats, ev["where"])
            return {"selectivity": sel, "rows": sel * self.stats["rows"], "analyzed": refreshed,
                    "path": access_path(self.stats, ev["where"], self.indexes, sel)}
        raise SystemExit(f"unknown op {op}")


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("run", "report"):
        raise SystemExit("usage: statsdb.py run|report <workload.json>")
    with open(sys.argv[2], encoding="utf-8") as fh:
        work = json.load(fh)
    table = Table(work["table"])
    results = []
    for ev in work["events"]:
        res = table.apply(ev)
        results.append(res)
        if sys.argv[1] == "run":
            continue
        if "stats" in res:
            for c, st in res["stats"]["columns"].items():
                print(f"analyze {c}: null_frac={st['null_frac']:.3f} ndv={st['ndv']} mcv={st['mcv']} histogram={st['histogram']}")
        elif "selectivity" in res:
            actual = sum(matches(r, ev["where"]) for r in table.rows())
            print(f"estimate {json.dumps(ev['where'])}: sel={res['selectivity']:.4f} rows={res['rows']:.1f} "
                  f"path={res['path']} analyzed={res['analyzed']} (rows matching now: {actual})")
        else:
            print(ev["op"], res)
    if sys.argv[1] == "run":
        print(json.dumps({"results": results}))

if __name__ == "__main__":
    main()
