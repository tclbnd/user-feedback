"""Evaluate numeric function calls.

    python3 sqlcall.py calls.json result.json

calls.json: [["round", "42.4382", 2], ["/", 7, 2], ["width_bucket", "5.35", "0.024", "10.06", 5], ...]
(first element is the function or operator, the rest are the arguments; a JSON integer is an
integer literal, a string is a numeric literal, null is SQL NULL).
Each result is {"call": ..., "value": "<psql text>"} or {"call": ..., "error": "..."}.
"""
import json
import sys

from numfn.core import FunctionError
from numfn.registry import FUNCTIONS


def call(c):
    try:
        return {"call": c, "value": FUNCTIONS[c[0]](*c[1:])}
    except FunctionError as exc:
        return {"call": c, "error": str(exc)}


def main():
    calls = json.load(open(sys.argv[1], encoding="utf-8"))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump([call(c) for c in calls], f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
