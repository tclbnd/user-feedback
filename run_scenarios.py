#!/usr/bin/env python3
"""Run the advisor on all three acceptance scenarios and print a compact table."""
import json
import os

import advisor
import workload

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples", "scenarios")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")


def main():
    os.makedirs(OUT, exist_ok=True)
    for name in ("read_heavy", "write_heavy", "mixed"):
        stats_doc = json.load(open(os.path.join(HERE, name + ".json"), encoding="utf-8"))
        groups = workload.read_log(os.path.join(HERE, name + ".log"))
        queries = [{**g, "parsed": workload.parse(g["sql"])} for g in groups]
        report = advisor.advise(stats_doc["tables"], queries, stats_doc["budget"])
        with open(os.path.join(OUT, name + ".report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
        print("=" * 20, name, "=" * 20)
        print("fingerprints:", len(queries), " executions:", sum(q["count"] for q in queries))
        print("recommended:", [(c["name"], c["columns"], c["size"]) for c in report["recommended"]])
        print("redundant:", report["redundant"])
        print("unused:", report["unused"])
        for q in report["queries"]:
            print(f"  x{q['count']:<5} {q['cost_before']:>12} -> {q['cost_after']:>12} "
                  f"benefit={q['benefit']:>14}  {q['fingerprint'][:70]}")


if __name__ == "__main__":
    main()
