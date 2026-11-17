#!/usr/bin/env python3
"""Hash join two JSON-lines files under a memory quota."""
import argparse
import json
import sys

from hashjoin import JOIN_TYPES, InMemoryJoin, JoinError, MemoryExceeded, key_of
from spill import DepthExceeded, PartitionedJoin


def read_rows(path):
    with open(path, encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise JoinError("%s: line %d: %s" % (path, number, exc)) from None
            if not isinstance(row, dict):
                raise JoinError("%s: line %d: expected a JSON object" % (path, number))
            yield row


def count_inputs(left_path, right_path, left_col, right_col):
    """Count rows on both sides and non-NULL build rows, validating keys."""
    build_rows = build_non_null = 0
    for row in read_rows(right_path):
        build_rows += 1
        if key_of(row, right_col) is not None:
            build_non_null += 1
    probe_rows = sum(1 for _ in read_rows(left_path))
    # Validate probe keys up front so bad input exits with status 2 even
    # when every probe row would have been discarded.
    for row in read_rows(left_path):
        key_of(row, left_col)
    return build_rows, build_non_null, probe_rows


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
    stats = dict(build_rows=0, probe_rows=0, spilled_partitions=0, max_depth=0)
    try:
        if "=" not in args.on or args.fanout < 2 or args.work_mem < 1:
            raise JoinError("--on must be left_column=right_column, fanout >= 2, work_mem >= 1")
        left_col, right_col = (c.strip() for c in args.on.split("=", 1))
        if not left_col or not right_col:
            raise JoinError("--on must be left_column=right_column")

        stats["build_rows"], build_non_null, stats["probe_rows"] = count_inputs(
            args.left, args.right, left_col, right_col
        )

        with open(args.output, "w", encoding="utf-8") as out:
            def emit(value):
                out.write(json.dumps(value, ensure_ascii=False) + "\n")

            if build_non_null <= args.work_mem:
                joiner = InMemoryJoin(
                    args.type, left_col, right_col, args.work_mem, emit
                )
                joiner.run(read_rows(args.right), read_rows(args.left))
            else:
                joiner = PartitionedJoin(
                    args.type, left_col, right_col, args.work_mem,
                    args.fanout, args.max_depth, emit, args.tmpdir
                )
                joiner.run(args.left, args.right)
                stats["spilled_partitions"] = joiner.spilled_partitions
                stats["max_depth"] = joiner.max_depth_seen
    except JoinError as exc:
        print("join: %s" % exc, file=sys.stderr)
        return 2
    except MemoryExceeded as exc:
        print("join: killed: %s" % exc, file=sys.stderr)
        return 137
    except DepthExceeded as exc:
        print("join: %s" % exc, file=sys.stderr)
        return 3
    if args.stats:
        with open(args.stats, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(stats) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
