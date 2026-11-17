#!/usr/bin/env python3
"""Page store CLI: replay a workload, recover after a crash, dump pages."""
import argparse
import json
import os
import sys

import pager
import recovery
from wal import Log


class Engine:
    def __init__(self, directory):
        os.makedirs(directory, exist_ok=True)
        self.pages = pager.PageFile(os.path.join(directory, "pages.db"))
        self.log = Log(os.path.join(directory, "wal.log"))
        self.buffer, self.dirty, self.att, self.imaged = {}, {}, {}, set()

    def page(self, pid):
        return self.buffer.setdefault(pid, self.pages.read(pid))

    def apply(self, step):
        op, tx = step["op"], step.get("tx")
        if op == "begin":
            self.att[tx] = self.log.append({"type": "BEGIN", "tx": tx, "prev": 0})["lsn"]
        elif op == "set":
            pid, page = step["page"], self.page(step["page"])
            if pid not in self.imaged:
                self.log.append({"type": "FPI", "page": pid, "image": pager.encode(page).hex()})
                self.imaged.add(pid)
            r = self.log.append({"type": "UPDATE", "tx": tx, "prev": self.att[tx], "page": pid,
                                 "slot": step["slot"], "before": page["slots"][step["slot"]],
                                 "after": step["value"]})
            page["slots"][step["slot"]], page["lsn"] = step["value"], r["lsn"]
            self.dirty.setdefault(pid, r["lsn"])
            self.att[tx] = r["lsn"]
        elif op == "commit":
            c = self.log.append({"type": "COMMIT", "tx": tx, "prev": self.att.pop(tx)})
            self.log.append({"type": "END", "tx": tx, "prev": c["lsn"]})
        elif op == "checkpoint":
            b = self.log.append({"type": "CKPT_BEGIN"})["lsn"]
            self.log.append({"type": "CKPT_END", "begin": b,
                             "att": {str(k): v for k, v in self.att.items()},
                             "dpt": {str(k): v for k, v in self.dirty.items()}})
            self.imaged.clear()
        elif op == "flush":
            pid = step["page"]
            raw = pager.encode(self.page(pid))
            if step.get("torn"):
                old = self.pages.read_raw(pid).ljust(pager.PAGE_SIZE, b"\0")
                raw = raw[:pager.PAGE_SIZE // 2] + old[pager.PAGE_SIZE // 2:]
            else:
                self.dirty.pop(pid, None)
            self.pages.write_raw(pid, raw)
        else:
            raise SystemExit(f"unknown op {op}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run", "recover", "dump"])
    parser.add_argument("directory")
    parser.add_argument("script", nargs="?")
    parser.add_argument("--crash-after-undo", type=int)
    args = parser.parse_args()
    if args.command == "run":
        engine = Engine(args.directory)
        for step in json.load(open(args.script, encoding="utf-8")):
            if step["op"] == "crash":
                break
            engine.apply(step)
    elif args.command == "recover":
        try:
            print(json.dumps(recovery.recover(args.directory, args.crash_after_undo)))
        except recovery.CrashPoint:
            sys.exit(75)
    else:
        pf = pager.PageFile(os.path.join(args.directory, "pages.db"))
        pages = {str(i): pf.read(i) for i in range(pf.count())}
        print(json.dumps({k: {"lsn": v["lsn"], "slots": v["slots"]} for k, v in pages.items()}, indent=1))


if __name__ == "__main__":
    main()
