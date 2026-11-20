"""Evaluate aggregate calls.

    python3 aggcall.py calls.json result.json

calls.json: [["avg", "int4", [1, 2, 4]], ["string_agg", "text", ["a", null, "b"], ","],
             ["percentile_cont", "int4", [1, 2, 3, 4], 0.5], ["count_star", "int4", [1, null]], ...]
Each call is [aggregate, input type, values, extra arguments...]; null is SQL NULL.
Each result is {"call": ..., "value": <psql text or null>} or {"call": ..., "error": "..."}.
"""
import json
import sys

from aggfn.aggregates import AGGREGATES, AggregateError


def call(c):
    try:
        return {"call": c, "value": AGGREGATES[c[0]](*c[1:])}
    except AggregateError as exc:
        return {"call": c, "error": str(exc)}


def main():
    calls = json.load(open(sys.argv[1], encoding="utf-8"))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump([call(c) for c in calls], f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
