#!/usr/bin/env python3
"""Restore a cluster to a point in time or a transaction boundary; show a restored copy."""
import argparse
import json
import os
import sys

import wal
from restore import RestoreError, restore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("restore")
    r.add_argument("cluster")
    r.add_argument("out")
    group = r.add_mutually_exclusive_group(required=True)
    group.add_argument("--to-time", type=int)
    group.add_argument("--to-tx", type=int)
    group.add_argument("--before-tx", type=int)
    s = sub.add_parser("show")
    s.add_argument("out")
    args = parser.parse_args()
    if args.cmd == "show":
        with open(os.path.join(args.out, "data.json"), encoding="utf-8") as fh:
            for name, rows in sorted(json.load(fh)["tables"].items()):
                print(name, json.dumps(rows, sort_keys=True))
        return 0
    if args.to_time is not None:
        target = ("time", args.to_time)
    else:
        target = ("tx", args.to_tx) if args.to_tx is not None else ("before", args.before_tx)
    try:
        summary = restore(args.cluster, args.out, target, wal.read(os.path.join(args.cluster, "wal")))
    except RestoreError as exc:
        print(json.dumps(exc.details))
        return 4
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
