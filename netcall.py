"""Evaluate inet/cidr calls.

    python3 netcall.py calls.json result.json

calls.json: [["host", "192.168.1.5/24"], ["<<", "192.168.1.5", "192.168.1.0/24"],
             ["abbrev", ["cidr", "10.1.0.0/16"]], ["+", "192.168.1.6", 25], ...]
A string argument is an inet literal; a nested list is a call whose result is the
argument (["cidr", "10.1/16"] makes a cidr value); integers are integers.
Each result is {"call": ..., "value": <psql text>} or {"call": ..., "error": "..."}.
"""
import json
import sys

from netaddr.functions import FUNCTIONS
from netaddr.types import NetError, output


def evaluate(c):
    args = [evaluate(a) if isinstance(a, list) else a for a in c[1:]]
    return FUNCTIONS[c[0]](*args)


def call(c):
    try:
        v = evaluate(c)
        return {"call": c, "value": output(v) if isinstance(v, tuple) else v}
    except NetError as exc:
        return {"call": c, "error": str(exc)}


def main():
    calls = json.load(open(sys.argv[1], encoding="utf-8"))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump([call(c) for c in calls], f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
