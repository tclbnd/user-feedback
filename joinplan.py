#!/usr/bin/env python3
"""Plan, explain and execute multi-table queries: joinplan.py plan|explain|exec <query.json>."""
import json
import operator
import sys

from cost import Query, table_of
from planner import choose, strip

OPS = {"=": operator.eq, "!=": operator.ne, "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


def passes(row, filters):
    return all(row[f["col"]] is not None and OPS[f["op"]](row[f["col"]], f["value"]) for f in filters)


def execute(node, q, data):
    if node["op"] == "scan":
        t = node["table"]
        rows = [{f"{t}.{c}": v for c, v in r.items()} for r in data[t]]
        return [r for r in rows if passes(r, node["filters"])]
    left, right = execute(node["left"], q, data), execute(node["right"], q, data)
    match = [(a, b) for a, b in node["on"]]
    out = []
    for lr in left:
        hits = [{**lr, **rr} for rr in right
                if all(lr.get(a, rr.get(a)) == rr.get(b, lr.get(b)) and lr.get(a, rr.get(a)) is not None
                       for a, b in match)]
        if not hits and node["type"] == "left":
            t = node["right"]["table"]
            hits = [{**lr, **{f"{t}.{c}": None for c in q.stats[t]["ndv"]}}]
        out += hits
    return [r for r in out if passes(r, node["filters"])]


def explain(node, depth=0):
    pad = "  " * depth
    if node["op"] == "scan":
        extra = " where " + ", ".join(f"{f['col']} {f['op']} {f['value']}" for f in node["filters"]) if node["filters"] else ""
        print(f"{pad}scan {node['table']}{extra}  rows={node['rows']:.1f} cost={node['cost']:.1f}")
        return
    on = " and ".join(f"{a} = {b}" for a, b in node["on"])
    print(f"{pad}{node['type']} join {on}  rows={node['rows']:.1f} cost={node['cost']:.1f}")
    explain(node["left"], depth + 1)
    explain(node["right"], depth + 1)


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("plan", "explain", "exec"):
        raise SystemExit("usage: joinplan.py plan|explain|exec <query.json>")
    with open(sys.argv[2], encoding="utf-8") as fh:
        raw = json.load(fh)
    q = Query(raw)
    plan = strip(choose(q))
    if sys.argv[1] == "plan":
        print(json.dumps({"plan": plan, "rows": plan["rows"], "cost": plan["cost"]}))
    elif sys.argv[1] == "explain":
        explain(plan)
    else:
        rows = execute(plan, q, raw["data"])
        cols = sorted({c for r in rows for c in r})
        out = sorted(([r.get(c) for c in cols] for r in rows), key=lambda r: json.dumps(r))
        print(json.dumps({"columns": cols, "rows": out}))


if __name__ == "__main__":
    main()
