#!/usr/bin/env python3
#!/usr/bin/env python3
"""Run a page access trace through the buffer pool, recover after a crash, dump pages."""
import argparse
import json
import os
import sys

import storage
from bufpool import BufferFull, BufferPool, Crash


def run(args):
    os.makedirs(args.dir, exist_ok=True)
    disk = storage.Disk(os.path.join(args.dir, "data.db"))
    wal = storage.Wal(os.path.join(args.dir, "wal.log"))
    pool = BufferPool(disk, wal, args.frames, args.k, args.crash_at_write)
    with open(args.trace, encoding="utf-8") as fh:
        trace = json.load(fh)
    errors = []
    try:
        for step, op in enumerate(trace):
            try:
                kind = op["op"]
                if kind == "get":
                    pool.fetch(op["page"])
                    pool.unpin(op["page"])
                elif kind == "scan":
                    for pid in range(op["from"], op["to"]):
                        pool.fetch(pid)
                        pool.unpin(pid)
                elif kind == "pin":
                    pool.fetch(op["page"])
                elif kind == "unpin":
                    pool.unpin(op["page"])
                elif kind == "write":
                    pool.modify(op["page"], op["slot"], op["value"])
                elif kind == "commit":
                    wal.flush()
                elif kind == "flush":
                    pool.flush_some(op.get("max", len(pool.frames)))
                elif kind == "checkpoint":
                    pool.checkpoint()
            except BufferFull:
                errors.append({"step": step, "error": "BUFFER_FULL"})
    except Crash:
        print(json.dumps({"crashed_at_write": pool.writes, **pool.stats, "errors": errors}))
        return 75
    print(json.dumps({**pool.stats, "errors": errors}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("dir")
    r.add_argument("trace")
    r.add_argument("--frames", type=int, default=4)
    r.add_argument("--k", type=int, default=2)
    r.add_argument("--crash-at-write", type=int)
    sub.add_parser("recover").add_argument("dir")
    sub.add_parser("dump").add_argument("dir")
    args = parser.parse_args()
    if args.cmd == "run":
        return run(args)
    if args.cmd == "recover":
        print(json.dumps(storage.recover(args.dir)))
        return 0
    disk = storage.Disk(os.path.join(args.dir, "data.db"))
    for pid in range(disk.count()):
        lsn, slots = disk.read(pid)
        print(pid, lsn, json.dumps(slots))
    return 0


if __name__ == "__main__":
    sys.exit(main())
