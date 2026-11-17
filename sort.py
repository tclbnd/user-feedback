#!/usr/bin/env python3
"""Sort JSON-lines rows by an ORDER BY clause under a memory quota."""
import argparse
import json
import sys

import keys
from extsort import MemoryExceeded, SortOperator


class BadInput(Exception):
    pass


def read_rows(path):
    with open(path, encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            if line.strip():
                try:
                    row = json.loads(line)
                except ValueError as exc:
                    raise BadInput(f"line {number}: {exc}") from None
                if not isinstance(row, dict):
                    raise BadInput(f"line {number}: expected a JSON object")
                yield row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--order", required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--work-mem", type=int, default=10000)
    parser.add_argument("--fan-in", type=int, default=8)
    parser.add_argument("--tmpdir")
    parser.add_argument("--stats")
    args = parser.parse_args()
    try:
        op = SortOperator(keys.parse(args.order), args.work_mem, args.fan_in, args.limit, args.tmpdir)
        with open(args.output, "w", encoding="utf-8") as out:
            for row in op.execute(read_rows(args.input)):
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
    except (BadInput, ValueError) as exc:
        print(f"sort: {exc}", file=sys.stderr)
        return 2
    except MemoryExceeded as exc:
        print(f"sort: killed: {exc}", file=sys.stderr)
        return 137
    if args.stats:
        with open(args.stats, "w", encoding="utf-8") as fh:
            json.dump(op.stats, fh)
            fh.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
