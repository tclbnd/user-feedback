"""Evaluate range calls.

    python3 rangecall.py calls.json result.json

calls.json: [["int4range", "[1,5]"], ["+", ["int4range", "[1,5)"], ["int4range", "[5,8)"]],
             ["@>", ["daterange", "[2026-11-01,2026-12-01)"], "2026-11-20"], ["upper", ["int4range", "[1,5]"]], ...]
["int4range" | "numrange" | "daterange", literal] makes a range; other lists are calls whose
arguments may be nested calls; a bare string or number next to a range is an element of its type.
Each result is {"call": ..., "value": <psql text or null>} or {"call": ..., "error": "..."}.
"""
import json
import sys

from ranges.operators import FUNCTIONS
from ranges.rangetype import SUBTYPES, Range, RangeError


def evaluate(c):
    if c[0] in SUBTYPES:
        return Range.parse(c[0], c[1])
    args = [evaluate(a) if isinstance(a, list) else a for a in c[1:]]
    typ = next((a.typ for a in args if isinstance(a, Range)), None)
    args = [SUBTYPES[typ](str(a)) if typ and not isinstance(a, Range) else a for a in args]
    return FUNCTIONS[c[0]](*args)


def call(c):
    try:
        v = evaluate(c)
        return {"call": c, "value": None if v is None else str(v)}
    except RangeError as exc:
        return {"call": c, "error": str(exc)}


def main():
    calls = json.load(open(sys.argv[1], encoding="utf-8"))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump([call(c) for c in calls], f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
