#!/usr/bin/env python3
"""Simulate a primary/replica cluster under partitions, crashes and clock drift."""
import argparse
import json
import os
import sys

from controller import Controller
from node import Node


class Cluster:
    def __init__(self, spec):
        self.spec = spec
        self.lease, self.drift = spec["lease"], spec.get("drift", 0.0)
        rates = spec.get("clock_rate", {})
        self.nodes = [Node(name, rates.get(name, 1.0)) for name in spec["nodes"]]
        self.ctl = Controller(self.lease)
        self.nodes[0].role = "primary"
        self.ctl.renew(self.nodes[0], 0)
        self.groups, self.trace = None, []

    def reach(self, a, b):
        names = {getattr(a, "name", a), getattr(b, "name", b)}
        if any(isinstance(x, Node) and not x.alive for x in (a, b)):
            return False
        return self.groups is None or any(names <= set(g) for g in self.groups)

    def node(self, name):
        return next(n for n in self.nodes if n.name == name)

    def push(self, t, leader):
        for other in self.nodes:
            if other is not leader and self.reach(leader, other) and not other.receive(leader):
                leader.step_down(other.term)
                self.trace.append({"t": t, "event": "step_down", "node": leader.name, "term": leader.term})
                return False
        return True

    def tick(self, t):
        for ev in self.spec.get("events", []):
            if ev["t"] == t:
                if "partition" in ev:
                    self.groups = ev["partition"]
                if ev.get("heal"):
                    self.groups = None
                if "crash" in ev:
                    self.node(ev["crash"]).alive = False
                if "restart" in ev:
                    n = self.node(ev["restart"])
                    n.alive, n.role = True, "replica"
        for n in self.nodes:
            if n.alive and n.role == "primary" and self.reach(n, "ctl") and not self.ctl.renew(n, t):
                n.step_down(self.ctl.term)
                self.trace.append({"t": t, "event": "step_down", "node": n.name, "term": n.term})
        winner = self.ctl.failover(t, [n for n in self.nodes if self.reach(n, "ctl")])
        if winner:
            self.trace.append({"t": t, "event": "promote", "node": winner.name, "term": winner.term})
        for w in self.spec.get("writes", []):
            if w["t"] != t:
                continue
            n, result = self.node(w["node"]), "rejected"
            if n.writable(t, self.lease, self.drift):
                n.log.append({"term": n.term, "key": w["key"], "value": w["value"]})
                result = "ok" if self.push(t, n) else "fenced"
            self.trace.append({"t": t, "node": n.name, "key": w["key"], "value": w["value"], "result": result})
        for n in self.nodes:
            if n.alive and n.role == "primary":
                self.push(t, n)

    def save(self, out):
        os.makedirs(os.path.join(out, "conflicts"), exist_ok=True)
        with open(os.path.join(out, "trace.jsonl"), "w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(r) + "\n" for r in self.trace)
        with open(os.path.join(out, "state.json"), "w", encoding="utf-8") as fh:
            json.dump({n.name: n.snapshot() for n in self.nodes}, fh, indent=1)
        for n in self.nodes:
            with open(os.path.join(out, "conflicts", f"{n.name}.jsonl"), "w", encoding="utf-8") as fh:
                fh.writelines(json.dumps(c) + "\n" for c in n.conflicts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("scenario")
    r.add_argument("out")
    s = sub.add_parser("show")
    s.add_argument("out")
    args = parser.parse_args()
    if args.cmd == "show":
        with open(os.path.join(args.out, "state.json"), encoding="utf-8") as fh:
            for name, st in json.load(fh).items():
                with open(os.path.join(args.out, "conflicts", f"{name}.jsonl"), encoding="utf-8") as c:
                    rolled = [json.loads(line) for line in c]
                print(name, st["role"], "term", st["term"], "kv", json.dumps(st["kv"], sort_keys=True),
                      "rolled back", [(c["index"], c["key"], c["value"]) for c in rolled])
        return 0
    with open(args.scenario, encoding="utf-8") as fh:
        spec = json.load(fh)
    cluster = Cluster(spec)
    for t in range(1, spec["ticks"] + 1):
        cluster.tick(t)
    cluster.save(args.out)
    for row in cluster.trace:
        if "event" in row or row["result"] != "ok":
            print(json.dumps(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
