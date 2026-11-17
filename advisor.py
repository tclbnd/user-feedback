#!/usr/bin/env python3
"""Workload-driven index advisor: recommend, find redundant and unused indexes, explain plans."""
import argparse
import json
import re

import cost
import workload


def total(stats, queries, indexes):
    return sum(q["count"] * cost.query_cost(stats, q["parsed"], indexes)[0] for q in queries)


def advise(stats, queries, budget):
    existing = [{**ix, "table": t} for t, d in stats.items() for ix in d.get("indexes", [])]
    pool = []
    for q in queries:
        for c in cost.candidates(stats, q["parsed"]):
            if c["name"] not in [p["name"] for p in pool] and c["columns"] not in [
                    e["columns"] for e in existing if e["table"] == c["table"]]:
                pool.append(c)
    chosen = []
    while len(chosen) < budget["max_indexes"]:
        base = total(stats, queries, existing + chosen)
        used_space = sum(cost.size(stats, c) for c in chosen)
        best = None
        for c in sorted(pool, key=lambda c: c["name"]):
            if c in chosen or used_space + cost.size(stats, c) > budget["space_bytes"]:
                continue
            gain = base - total(stats, queries, existing + chosen + [c])
            if gain > 1e-9 and (best is None or gain > best[0] + 1e-9):
                best = (gain, c)
        if best is None:
            break
        chosen.append(best[1])
    final = existing + chosen
    report = []
    used = set()
    for q in queries:
        before = cost.query_cost(stats, q["parsed"], existing)[0]
        after, names = cost.query_cost(stats, q["parsed"], final)
        used |= set(names)
        report.append({"fingerprint": q["fingerprint"], "count": q["count"], "cost_before": round(before, 2),
                       "cost_after": round(after, 2), "benefit": round((before - after) * q["count"], 2)})
    redundant = []
    for e in existing:
        if e.get("unique"):
            continue
        covered = any(o is not e and o["table"] == e["table"]
                      and o["columns"][:len(e["columns"])] == e["columns"]
                      and (len(o["columns"]) > len(e["columns"]) or o["name"] < e["name"]) for o in final)
        if covered:
            redundant.append(e["name"])
    unused = [e["name"] for e in existing
              if not e.get("unique") and e["name"] not in redundant and e["name"] not in used]
    return {"recommended": [{**c, "size": cost.size(stats, c)} for c in chosen], "queries": report,
            "redundant": redundant, "unused": unused}


def explain(data, sql, extra):
    stats = {t: {"rows": len(d["rows"]), "columns": {c: {"ndv": max(1, len({r.get(c) for r in d["rows"]})), "width": w}
                                                     for c, w in d["columns"].items()}}
             for t, d in data["tables"].items()}
    indexes = [{**ix, "table": t} for t, d in data["tables"].items() for ix in d.get("indexes", [])] + extra
    q = workload.parse(sql)
    cost_, used = cost.query_cost(stats, q, indexes)
    rows = [r for r in data["tables"][q["table"]]["rows"] if all(matches(r, c, sql) for _, c, _ in q["preds"])]
    return {"access": used[0] if used else "seq", "cost": round(cost_, 2), "rows": len(rows)}


def matches(row, col, sql):
    m = re.search(rf"\b{col}\s*(=|<=|>=|<|>|BETWEEN)\s*('[^']*'|-?\d+)(?:\s+AND\s+(-?\d+))?", sql, re.I)
    op, lit = m.group(1).upper(), json.loads(m.group(2).replace("'", '"'))
    v = row.get(col)
    if v is None:
        return False
    if op == "BETWEEN":
        return lit <= v <= int(m.group(3))
    return {"=": v == lit, "<": v < lit, ">": v > lit, "<=": v <= lit, ">=": v >= lit}[op]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["advise", "explain"])
    ap.add_argument("data", help="stats.json for advise, data.json for explain")
    ap.add_argument("arg", help="slow query log for advise, SQL text for explain")
    ap.add_argument("--indexes", help="explain: JSON list of extra indexes, or an advise output file")
    ap.add_argument("--out", help="advise: also write the report to this file")
    args = ap.parse_args()
    data = json.load(open(args.data, encoding="utf-8"))
    if args.command == "advise":
        queries = [{**g, "parsed": workload.parse(g["sql"])} for g in workload.read_log(args.arg)]
        report = json.dumps(advise(data["tables"], queries, data["budget"]), indent=1)
        print(report)
        if args.out:
            open(args.out, "w", encoding="utf-8").write(report + "\n")
    else:
        extra = json.load(open(args.indexes, encoding="utf-8")) if args.indexes else []
        extra = extra["recommended"] if isinstance(extra, dict) else extra
        print(json.dumps(explain(data, args.arg, extra)))


if __name__ == "__main__":
    main()
