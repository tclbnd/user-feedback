#!/usr/bin/env python3
"""Hash join two JSON-lines files under a memory quota."""
import argparse
import json
import sys

from hashjoin import JOIN_TYPES, InMemoryJoin, JoinError, MemoryExceeded
from spill import nested_loop


def read_rows(path):
    with open(path, encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise JoinError(f"{path}: line {number}: {exc}") from None
            if not isinstance(row, dict):
                raise JoinError(f"{path}: line {number}: expected a JSON object")
            yield row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("left")
    parser.add_argument("right")
    parser.add_argument("output")
    parser.add_argument("--type", required=True, choices=JOIN_TYPES)
    parser.add_argument("--on", required=True)
    parser.add_argument("--work-mem", type=int, default=10000)
    parser.add_argument("--fanout", type=int, default=8)
    parser.add_argument("--max-depth", type=int, default=4)
    parser.add_argument("--tmpdir")
    parser.add_argument("--stats")
    args = parser.parse_args()
    stats = {"build_rows": 0, "probe_rows": 0, "spilled_partitions": 0, "max_depth": 0}
    try:
        if "=" not in args.on or args.fanout < 2 or args.work_mem < 1:
            raise JoinError("--on must be left_column=right_column, fanout >= 2, work_mem >= 1")
        left_col, right_col = (c.strip() for c in args.on.split("=", 1))
        with open(args.output, "w", encoding="utf-8") as out:
            emit = lambda value: out.write(json.dumps(value, ensure_ascii=False) + "\n")  # noqa: E731
            joiner = InMemoryJoin(args.type, left_col, right_col, args.work_mem, emit)
            stats["build_rows"] = sum(1 for _ in read_rows(args.right))
            stats["probe_rows"] = sum(1 for _ in read_rows(args.left))
            if stats["build_rows"] <= args.work_mem:
                joiner.run(read_rows(args.right), read_rows(args.left))
            else:
                nested_loop(joiner, args.left, args.right)
    except JoinError as exc:
        print(f"join: {exc}", file=sys.stderr)
        return 2
    except MemoryExceeded as exc:
        print(f"join: killed: {exc}", file=sys.stderr)
        return 137
    if args.stats:
        with open(args.stats, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(stats) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
