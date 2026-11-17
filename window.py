#!/usr/bin/env python3
"""Compute window functions over a JSON-lines table."""
import argparse
import json
import sys

from frames import sort_partition
from functions import evaluate


def run(rows, spec, recompute=False):
    stats = {"agg_updates": 0}
    parts = {}
    for index, row in enumerate(rows):
        key = json.dumps([row.get(c) for c in spec.get("partition_by", [])])
        parts.setdefault(key, []).append((index, row))
    out = [dict(row) for row in rows]
    order_by = spec.get("order_by", [])
    for members in parts.values():
        ordered = sort_partition([dict(row, _pos=index) for index, row in members], order_by)
        for func in spec["functions"]:
            for row, value in zip(ordered, evaluate(ordered, order_by, func, stats, recompute)):
                out[row["_pos"]][func["as"]] = value
    return out, stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("rows")
    parser.add_argument("spec")
    parser.add_argument("output")
    parser.add_argument("--recompute", action="store_true")
    parser.add_argument("--stats")
    args = parser.parse_args()
    with open(args.rows, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    with open(args.spec, encoding="utf-8") as fh:
        spec = json.load(fh)
    out, stats = run(rows, spec, args.recompute)
    with open(args.output, "w", encoding="utf-8") as fh:
        for row in out:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    if args.stats:
        with open(args.stats, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(stats) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
