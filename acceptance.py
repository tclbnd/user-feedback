#!/usr/bin/env python3
"""Acceptance harness for the three required workload shapes.

For each scenario it checks:
  * fingerprints aggregate all statements (counts sum to the log total);
  * recommended indexes respect max_indexes and the space budget;
  * per-query benefit equals (cost_before - cost_after) * count, and the
    workload totals match an independent recomputation;
  * redundant indexes are true ordered-prefix covers; unused indexes are
    neither redundant nor used by any cheapest plan;
  * write statements show negative benefit when a recommended index touches
    their table (write amplification is actually charged);
  * read-heavy -> several indexes, write-heavy -> none, mixed -> some.
"""
import json
import os

import advisor
import cost
import workload

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples", "scenarios")
EXPECT = {
    "read_heavy": {"min_rec": 2},
    "write_heavy": {"rec": 0},
    "mixed": {"min_rec": 1},
}


def check(name):
    doc = json.load(open(os.path.join(HERE, name + ".json"), encoding="utf-8"))
    groups = workload.read_log(os.path.join(HERE, name + ".log"))
    queries = [{**g, "parsed": workload.parse(g["sql"])} for g in groups]
    stats, budget = doc["tables"], doc["budget"]
    report = advisor.advise(stats, queries, budget)
    failures = []

    def fail(msg):
        failures.append(msg)

    # 1. counts
    with open(os.path.join(HERE, name + ".log"), encoding="utf-8") as f:
        log_executions = sum(
            len(workload.split_statements(line)) for line in f
            if line.strip() and not line.startswith("#"))
    if sum(q["count"] for q in report["queries"]) != log_executions:
        fail("fingerprint counts do not sum to log statements")

    # 2. budgets
    if len(report["recommended"]) > budget["max_indexes"]:
        fail("max_indexes exceeded")
    if sum(c["size"] for c in report["recommended"]) > budget["space_bytes"]:
        fail("space budget exceeded")

    # 3. independent recomputation of every benefit and workload total
    existing = [{**ix, "table": t} for t, d in stats.items() for ix in d.get("indexes", [])]
    final = existing + report["recommended"]
    total_before = total_after = 0.0
    for entry, q in zip(report["queries"], queries):
        before = cost.query_cost(stats, q["parsed"], existing)[0]
        after, _ = cost.query_cost(stats, q["parsed"], final)
        total_before += before * q["count"]
        total_after += after * q["count"]
        if round(before, 2) != entry["cost_before"] or round(after, 2) != entry["cost_after"]:
            fail(f"cost mismatch on {entry['fingerprint'][:40]}")
        if round((before - after) * q["count"], 2) != entry["benefit"]:
            fail(f"benefit mismatch on {entry['fingerprint'][:40]}")
    net = round(total_before - total_after, 2)
    if net != round(sum(q["benefit"] for q in report["queries"]), 2):
        fail("net workload benefit does not match per-query sum")

    # 4. redundant = ordered prefix of another final index; never a unique one
    by_name = {ix["name"]: ix for ix in final}
    for rname in report["redundant"]:
        ix = by_name[rname]
        if ix.get("unique"):
            fail("unique index flagged redundant")
        if not any(o["name"] != rname and o["table"] == ix["table"]
                   and o["columns"][:len(ix["columns"])] == ix["columns"]
                   and (len(o["columns"]) > len(ix["columns"]) or o["name"] < rname)
                   for o in final):
            fail(f"{rname} is not an ordered-prefix cover")

    # 5. unused = non-unique, not redundant, absent from every cheapest plan
    used = set()
    for q in queries:
        _, names = cost.query_cost(stats, q["parsed"], final)
        used.update(names)
    for uname in report["unused"]:
        if uname in report["redundant"] or uname in used:
            fail(f"{uname} flagged unused but is redundant or used")

    # 6. write amplification: a write touching a recommended table costs more after
    write_penalties_seen = 0
    for entry, q in zip(report["queries"], queries):
        if q["parsed"]["kind"] != "select":
            touched = any(c["table"] == q["parsed"]["table"] for c in report["recommended"])
            if touched and entry["benefit"] < 0:
                write_penalties_seen += 1
    if report["recommended"] and write_penalties_seen == 0 and any(
            q["parsed"]["kind"] != "select" for q in queries):
        fail("no write penalty observed despite recommended indexes on written tables")

    # 7. scenario shape
    n = len(report["recommended"])
    if "rec" in EXPECT[name] and n != EXPECT[name]["rec"]:
        fail(f"expected {EXPECT[name]['rec']} recommendations, got {n}")
    if "min_rec" in EXPECT[name] and n < EXPECT[name]["min_rec"]:
        fail(f"expected at least {EXPECT[name]['min_rec']} recommendations, got {n}")

    print(f"--- {name}: executions={log_executions} fingerprints={len(queries)} "
          f"recommended={n} redundant={report['redundant']} unused={report['unused']} "
          f"net_benefit={net} write_penalties={write_penalties_seen}")
    if failures:
        for f in failures:
            print("  FAIL:", f)
        raise SystemExit(f"{name} acceptance failed")
    print("  all checks passed")


if __name__ == "__main__":
    for scenario in ("read_heavy", "write_heavy", "mixed"):
        check(scenario)
    print("\nACCEPTANCE OK")
