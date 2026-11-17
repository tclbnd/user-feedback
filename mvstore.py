#!/usr/bin/env python3
"""Run a multi-session script against the store."""
import json
import sys

from mvcc import Store
from txn import TxnManager


def run(steps):
    tm = TxnManager()
    store, sessions, out = Store(tm), {}, []
    for step in steps:
        op = step["op"]
        if op == "begin":
            sessions[step["s"]] = tm.begin()
            out.append({"op": op, "s": step["s"], "xid": sessions[step["s"]][0]})
        elif op == "read":
            xid, snap = sessions[step["s"]]
            out.append({"op": op, "s": step["s"], "key": step["key"],
                        "value": store.read(step["key"], xid, snap)})
        elif op == "write":
            xid, snap = sessions[step["s"]]
            store.write(step["key"], step["value"], xid, snap)
        elif op in ("commit", "abort"):
            xid, _ = sessions.pop(step["s"])
            tm.finish(xid, "committed" if op == "commit" else "aborted")
        elif op == "vacuum":
            out.append({"op": op, "removed": store.vacuum()})
        elif op == "chain":
            out.append({"op": op, "key": step["key"], "versions": [
                [v["xmin"], v["xmax"], v["value"]] for v in store.chains.get(step["key"], [])]})
        elif op == "next_xid":
            tm.set_next(step["value"])
        else:
            raise SystemExit(f"unknown op {op}")
    return out


if __name__ == "__main__":
    print(json.dumps(run(json.load(open(sys.argv[1], encoding="utf-8")))))
