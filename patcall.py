"""Evaluate pattern matching expressions.

    python3 patcall.py calls.json result.json

calls.json: [["like", "abc", "a%"], ["like", "a%c", "a\\%c"], ["like", "a%c", "a#%c", "#"],
             ["ilike", "ABC", "a%"], ["similar", "abc", "(a|b)%"], ["substring_similar", "foobar", "%#\"o_b#\"%", "#"],
             ["~", "abc", "b"], ["~*", "ABC", "b"], ["!~", "abc", "x"],
             ["regexp_match", "foobarbequebaz", "ba."], ["regexp_split_to_array", "a b  c", "\\s+"]]
"""
import json
import sys

from patx.like import PatternError, like, similar, substring_similar
from patx.posix import match_op, regexp_match, regexp_split_to_array


def call(c):
    name, a = c[0], c[1:]
    try:
        if name == "like":
            v = like(a[0], a[1], *a[2:3])
        elif name == "ilike":
            v = like(a[0], a[1], *a[2:3], icase=True)
        elif name == "similar":
            v = similar(*a)
        elif name == "substring_similar":
            v = substring_similar(*a)
        elif name == "~":
            v = match_op(a[0], a[1])
        elif name == "~*":
            v = match_op(a[0], a[1], icase=True)
        elif name == "!~":
            v = match_op(a[0], a[1], negate=True)
        elif name == "regexp_match":
            v = regexp_match(*a)
        elif name == "regexp_split_to_array":
            v = regexp_split_to_array(*a)
        else:
            raise PatternError(f"function {name} does not exist")
        return {"call": c, "value": v}
    except (PatternError, Exception) as exc:
        return {"call": c, "error": f"{type(exc).__name__}: {exc}"}


def main():
    calls = json.load(open(sys.argv[1], encoding="utf-8"))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump([call(c) for c in calls], f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
