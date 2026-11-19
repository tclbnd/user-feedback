"""Evaluate string function calls.

    python3 sqlcall.py calls.json result.json

calls.json: [["concat", "a", null, "b"], ["lpad", "hi", 5, "xy"], ["to_hex", -1, "bigint"], ...]
(first element is the function name, the rest are the arguments; null is SQL NULL).
Each result is {"call": ..., "value": ...} or {"call": ..., "error": "..."}.
"""
import json
import sys

from strfn.basic import FunctionError
from strfn.registry import FUNCTIONS


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
