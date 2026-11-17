#!/usr/bin/env python3
"""Sort JSON-lines rows by an ORDER BY clause under a memory quota."""
import argparse
import json
import signal
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


def _terminate(signum, _frame):
    # Unwind the stack so the operator's ``finally`` clauses remove temporary
    # files; ``SystemExit`` propagates out of main.
    raise SystemExit(128 + signum)


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
    rows = None
    op = None
    try:
        op = SortOperator(
            keys.parse(args.order), args.work_mem, args.fan_in, args.limit, args.tmpdir
        )
        # Clean up temporary files if we are interrupted mid-sort.
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            try:
                signal.signal(signum, _terminate)
            except (ValueError, OSError):
                pass
        rows = op.execute(read_rows(args.input))
        with open(args.output, "w", encoding="utf-8") as out:
            for row in rows:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
        if args.stats:
            with open(args.stats, "w", encoding="utf-8") as fh:
                json.dump(op.stats, fh)
                fh.write("\n")
    except (BadInput, ValueError) as exc:
        print(f"sort: {exc}", file=sys.stderr)
        return 2
    except MemoryExceeded as exc:
        print(f"sort: killed: {exc}", file=sys.stderr)
        return 137
    except OSError as exc:
        print(f"sort: {exc}", file=sys.stderr)
        return 2
    finally:
        # Close the generator first: its ``finally`` removes the temporary
        # files and releases the rows it still holds.
        if rows is not None:
            rows.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
