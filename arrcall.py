"""Evaluate array function calls.

    python3 arrcall.py calls.json result.json

calls.json: [["array_append", [1, 2], 3], ["cardinality", []], ["slice", [1, 2, 3], 2, 9],
             ["string_to_array", "a,b,,c", ","], ["array_out", ["a b", null]], ...]
Arrays are JSON lists (null = NULL element, a bare null argument = NULL).
"""
import json
import sys

from arr import core, text
from arr.core import ArrayError

FUNCS = {name: getattr(core, name) for name in (
    "cardinality", "array_length", "array_lower", "array_upper", "array_append", "array_prepend", "array_cat",
    "array_remove", "array_replace", "array_position", "array_positions", "trim_array")}
FUNCS.update({"subscript": core.subscript, "slice": core.slice_, "@>": core.contains, "&&": core.overlaps,
              "array_to_string": text.array_to_string, "string_to_array": text.string_to_array,
              "array_out": text.array_out})


def call(c):
    """Evaluate one call; errors become results, never abort the batch."""
    try:
        if not isinstance(c, list) or not c:
            raise ArrayError("call must be a non-empty array of [function, args...]")
        if c[0] not in FUNCS:
            raise ArrayError("unknown function %r" % (c[0],))
        return {"call": c, "value": FUNCS[c[0]](*c[1:])}
    except ArrayError as exc:
        return {"call": c, "error": str(exc)}
    except TypeError as exc:
        # Wrong number of arguments for the function.
        return {"call": c, "error": str(exc)}


def main():
    calls = json.load(open(sys.argv[1], encoding="utf-8"))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump([call(c) for c in calls], f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
