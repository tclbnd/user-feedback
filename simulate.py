#!/usr/bin/env python3
"""Replay a read/write scenario against the simulated cluster."""
import argparse
import json
import sys

from cluster import Cluster
from router import Router


class Session:
    def __init__(self):
        self.last_write = 0
        self.last_read = 0
        self.txn = None

    def required(self):
        # Read-your-writes plus monotonic reads: the serving node must have
        # replayed every commit this session made and every version it read.
        return max(self.last_write, self.last_read)


def run(scenario):
    cluster = Cluster(scenario["replicas"])
    router = Router(cluster, scenario.get("policy", {}))
    sessions, reads = {}, []
    for i, e in enumerate(scenario["events"]):
        op, t = e["op"], e["t"]
        if op in ("set_lag", "down", "up"):
            replica = cluster.by_name[e["replica"]]
            if op == "set_lag":
                replica.lag = e["lag"]
            else:
                replica.up = op == "up"
            continue
        s = sessions.setdefault(e["session"], Session())
        if op == "begin":
            s.txn = []
        elif op == "commit":
            if s.txn:
                s.last_write = cluster.commit(t, s.txn)
            s.txn = None
        elif op == "write":
            if s.txn is not None:
                s.txn.append((e["key"], e["value"]))
            else:
                s.last_write = cluster.commit(t, [(e["key"], e["value"])])
        elif op == "read":
            if s.txn is not None:
                # Rule 1: every read inside a transaction goes to the
                # primary immediately; a key written in this txn shows the
                # buffered value with a null version.
                target, served = "primary", t
                result = cluster.read("primary", e["key"], t)
                failed = []
                own = [v for k, v in s.txn if k == e["key"]]
                if own:
                    result = dict(result, value=own[-1], version=None)
            else:
                target, served, result, failed = router.read(
                    e["key"], t, s.required())
            s.last_read = max(s.last_read, result["applied"])
            reads.append({"i": i, "session": e["session"], "target": target,
                          "served_at": served, "value": result["value"],
                          "version": result["version"], "failed": failed})
        else:
            raise SystemExit(f"unknown op {op!r} in event {i}")
    return {"reads": reads}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario")
    parser.add_argument("output")
    args = parser.parse_args()
    with open(args.scenario, encoding="utf-8") as fh:
        scenario = json.load(fh)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(run(scenario), fh, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
