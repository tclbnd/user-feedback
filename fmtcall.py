"""Evaluate to_char calls.

    python3 fmtcall.py calls.json result.json

calls.json: [["to_char", 1485, "9,999"], ["to_char", "148.5", "999.999"], ["to_char", -485, "999PR"], ...]
(a JSON integer is an integer value, a string is a numeric literal, null is SQL NULL).
Each result is {"call": ..., "value": ...} or {"call": ..., "error": "..."}.
"""
import json
import sys

from numfmt.to_char import FormatError, to_char

FUNCTIONS = {"to_char": to_char}


def call(c):
    try:
        return {"call": c, "value": FUNCTIONS[c[0]](*c[1:])}
    except FormatError as exc:
        return {"call": c, "error": str(exc)}


def main():
    calls = json.load(open(sys.argv[1], encoding="utf-8"))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump([call(c) for c in calls], f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
