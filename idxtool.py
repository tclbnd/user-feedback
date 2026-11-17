#!/usr/bin/env python3
"""Check and repair the secondary indexes of a table, or query it by index and by scan."""
import argparse
import json
import sys

from check import Crash, check, repair
from heap import Table


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").add_argument("dir")
    r = sub.add_parser("repair")
    r.add_argument("dir")
    r.add_argument("--writes")
    r.add_argument("--crash-after", type=int)
    q = sub.add_parser("query")
    for name in ("dir", "index", "key"):
        q.add_argument(name)
    args = parser.parse_args()
    table = Table(args.dir)
    if args.cmd == "check":
        print(json.dumps({"problems": check(table)}, indent=1))
    elif args.cmd == "repair":
        writes = []
        if args.writes:
            with open(args.writes, encoding="utf-8") as fh:
                writes = json.load(fh)
        try:
            summary = repair(table, writes, args.crash_after)
        except Crash:
            return 75
        print(json.dumps({**summary, "remaining": check(Table(args.dir))}))
    else:
        ix = next(i for i in table.indexes if i["name"] == args.index)
        via_index = sorted(table.row(rid) for key, rid in table.entries[args.index] if key == args.key
                           if table.row(rid) is not None)
        via_scan = sorted(row for _, row in table.rows() if table.value(row, ix) == args.key)
        print("index:", json.dumps(via_index))
        print("scan: ", json.dumps(via_scan))
    return 0


if __name__ == "__main__":
    sys.exit(main())
