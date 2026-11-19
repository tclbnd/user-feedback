"""Evaluate jsonb modification calls.

    python3 jcall.py calls.json result.json

calls.json: [["||", "<jsonb>", "<jsonb>"], ["-", "<jsonb>", "key" | 3 | ["k1", "k2"]],
             ["#-", "<jsonb>", ["a", "0"]], ["jsonb_set", "<jsonb>", ["a", "b"], "<jsonb>", true],
             ["jsonb_insert", "<jsonb>", ["0"], "<jsonb>", false], ["jsonb_strip_nulls", "<jsonb>"],
             ["jsonb_array_length", "<jsonb>"], ["jsonb_typeof", "<jsonb>"]]
jsonb arguments are JSON text (null = SQL NULL); results are jsonb text, numbers or errors.
"""
import json
import sys

from jmod import ops
from jmod.value import JsonbError, parse, render


def call(c):
    name, args = c[0], c[1:]
    try:
        if name == "||":
            v = render(ops.concat(parse(args[0]), parse(args[1])))
        elif name == "-":
            v = render(ops.delete(parse(args[0]), args[1]))
        elif name == "#-":
            v = render(ops.delete_path(parse(args[0]), args[1]))
        elif name == "jsonb_set":
            v = render(ops.jsonb_set(parse(args[0]), args[1], parse(args[2]), *args[3:]))
        elif name == "jsonb_insert":
            v = render(ops.jsonb_insert(parse(args[0]), args[1], parse(args[2]), *args[3:]))
        elif name == "jsonb_strip_nulls":
            v = render(ops.strip_nulls(parse(args[0])))
        elif name == "jsonb_array_length":
            v = ops.array_length(parse(args[0]))
        elif name == "jsonb_typeof":
            v = ops.typeof(parse(args[0]))
        elif name == "jsonb":
            v = render(parse(args[0]))
        else:
            raise JsonbError(f"function {name} does not exist")
        return {"call": c, "value": v}
    except JsonbError as exc:
        return {"call": c, "error": str(exc)}
    except RecursionError:
        return {"call": c, "error": "stack depth limit exceeded"}


def main():
    calls = json.load(open(sys.argv[1], encoding="utf-8"))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump([call(c) for c in calls], f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
