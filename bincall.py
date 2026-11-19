"""Evaluate bytea / bit string calls.

    python3 bincall.py calls.json result.json

calls.json: [["encode", "\\x00615c", "escape"], ["get_byte", "\\x1234567890", 4],
             ["&", "B'10001'", "B'01101'"], ["bit", "B'101'", 5], ["decode", "MTIz", "base64"], ...]
Arguments: "\\x..." is a bytea literal, "B'...'" a bit string literal, other strings are text,
JSON integers are integers, null is NULL (every function here is strict).
Each result is {"call": ..., "value": <psql text>} or {"call": ..., "error": "..."}.
"""
import inspect
import json
import sys

from binfn.functions import FUNCTIONS
from binfn.values import BinError, parse, show, type_name


def call(c):
    try:
        args = [parse(a) for a in c[1:]]
        func = FUNCTIONS.get(c[0])
        if func is None:
            raise BinError("function %s(%s) does not exist"
                           % (c[0], ", ".join(type_name(a) for a in args)))
        try:
            inspect.signature(func).bind(*args)
        except TypeError:
            raise BinError("function %s(%s) does not exist"
                           % (c[0], ", ".join(type_name(a) for a in args)))
        if any(a is None for a in args):
            return {"call": c, "value": None}
        return {"call": c, "value": show(func(*args))}
    except BinError as exc:
        return {"call": c, "error": str(exc)}


def main():
    calls = json.load(open(sys.argv[1], encoding="utf-8"))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump([call(c) for c in calls], f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
