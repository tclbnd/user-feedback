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
        # Every rate must lie within 1 +/- drift (README).  When the scenario
        # does not state a drift, use the one its clock rates imply.
        if "drift" not in spec:
            self.drift = round(max([abs(n.rate - 1.0) for n in self.nodes] + [0.0]), 9)
        for node in self.nodes:
            if abs(node.rate - 1.0) > self.drift + 1e-12:
                raise ValueError("clock rate of {0} outside 1 +/- drift".format(node.name))
        self.nodes[0].role = "primary"
        self.ctl.renew(self.nodes[0], 0)
        self.groups, self.trace = None, []
        self.holders, self.accepted, self.reconciliations = {}, {}, []

    def reach(self, a, b):
        names = {getattr(a, "name", a), getattr(b, "name", b)}
        if any(isinstance(x, Node) and not x.alive for x in (a, b)):
            return False
        return self.groups is None or any(names <= set(g) for g in self.groups)

    def node(self, name):
        return next(n for n in self.nodes if n.name == name)

    def apply_events(self, t):
        for ev in self.spec.get("events", []):
            if ev["t"] != t:
                continue
            if "partition" in ev:
                self.groups = [list(g) for g in ev["partition"]]
            if ev.get("heal"):
                self.groups = None
            if "crash" in ev:
                # A crashed node does nothing: it is not a writer and it cannot
                # be reached, so drop the role as well as the lease.
                n = self.node(ev["crash"])
                n.alive, n.role, n.lease_start = False, "replica", None
            if "restart" in ev:
                # A restarted node keeps log and term and comes back as a
                # replica without a lease; reconciliation is a push away.
                n = self.node(ev["restart"])
                n.alive, n.role, n.lease_start = True, "replica", None

    def renew(self, t):
        for n in self.nodes:
            if not n.alive or n.role != "primary":
                continue
            if self.reach(n, "ctl"):
                if not self.ctl.renew(n, t):
                    self.record(t, "step_down", n, n.step_down(self.ctl.term))
            elif not n.lease_valid(t, self.lease, self.drift):
                # Cut off from the controller and out of lease: stop pushing a
                # possibly stale log around, even if no newer term reached us.
                term = n.term
                n.resign()
                self.record(t, "step_down", n, term)

    def failover(self, t):
        candidates = [n for n in self.nodes if n.alive and self.reach(n, "ctl")]
        winner = self.ctl.failover(t, candidates)
        if winner:
            self.record(t, "promote", winner, winner.term)
        return winner

    def push(self, t, leader):
        """Replicate the leader's log with the storage-layer fence.

        1. If any reachable node already adopted a newer term, this leader is
           stale: it steps down before touching anybody's log, so a zombie
           primary can never overwrite a replica that heard the news.
        2. Otherwise copy the log to every reachable node.  A recipient still
           refuses if it is a primary of the same term (two primaries in one
           term is a bug, not a state to reconcile); then the sender steps
           down as well.
        """
        peers = [o for o in self.nodes if o is not leader and self.reach(leader, o)]
        newer = [o.term for o in peers if o.term > leader.term]
        if newer:
            self.record(t, "step_down", leader, leader.step_down(max(newer)))
            return False
        for other in peers:
            record = other.receive(leader)
            if not record:
                self.record(t, "step_down", leader, leader.step_down(other.term))
                return False
            if record["rolled_back"]:
                # trace.jsonl keeps its fixed schema, so rollbacks are reported
                # through summary.json and stdout instead of a trace event.
                self.reconciliations.append(
                    {"t": t, "leader": leader.name, "node": other.name, "term": other.term,
                     "fork": record["fork"], "rolled_back": record["rolled_back"]})
        return True

    def record(self, t, event, node, term):
        self.trace.append({"t": t, "event": event, "node": node.name, "term": term})

    def write(self, t, w):
        n, result = self.node(w["node"]), "rejected"
        if n.writable(t, self.lease, self.drift):
            # Append first: an entry accepted on a valid lease is a local fact.
            # If replication is fenced the entry stays in the log and a newer
            # primary will export it to `conflicts/` when it takes over.
            n.log.append({"term": n.term, "key": w["key"], "value": w["value"]})
            if self.push(t, n):
                result = "ok"
                self.accepted.setdefault(t, []).append(n.name)
            else:
                result = "fenced"
        self.trace.append({"t": t, "node": n.name, "key": w["key"],
                           "value": w["value"], "result": result})

    def tick(self, t):
        self.apply_events(t)
        self.renew(t)
        self.failover(t)
        # Every node whose lease is still valid at this instant; safety
        # requires at most one of them.
        self.holders.setdefault(t, [n.name for n in self.nodes
                                    if n.alive and n.role == "primary"
                                    and n.lease_valid(t, self.lease, self.drift)])
        for w in self.spec.get("writes", []):
            if w["t"] == t:
                self.write(t, w)
        for n in self.nodes:
            if n.alive and n.role == "primary":
                self.push(t, n)

    def summary(self):
        return {"lease_holders": {str(k): v for k, v in self.holders.items()},
                "accepted": {str(k): v for k, v in self.accepted.items()},
                "reconciliations": self.reconciliations,
                "final_partition": self.groups}

    def save(self, out):
        os.makedirs(os.path.join(out, "conflicts"), exist_ok=True)
        with open(os.path.join(out, "trace.jsonl"), "w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(r) + "\n" for r in self.trace)
        with open(os.path.join(out, "state.json"), "w", encoding="utf-8") as fh:
            json.dump({n.name: n.snapshot() for n in self.nodes}, fh, indent=1)
        with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as fh:
            json.dump(self.summary(), fh, indent=1)
        for n in self.nodes:
            with open(os.path.join(out, "conflicts", f"{n.name}.jsonl"), "w", encoding="utf-8") as fh:
                fh.writelines(json.dumps(c) + "\n" for c in n.conflicts)


def load(out):
    with open(os.path.join(out, "trace.jsonl"), encoding="utf-8") as fh:
        trace = [json.loads(line) for line in fh if line.strip()]
    with open(os.path.join(out, "state.json"), encoding="utf-8") as fh:
        state = json.load(fh)
    conflicts = {}
    cdir = os.path.join(out, "conflicts")
    if os.path.isdir(cdir):
        for name in sorted(os.listdir(cdir)):
            if name.endswith(".jsonl"):
                with open(os.path.join(cdir, name), encoding="utf-8") as fh:
                    conflicts[name[:-len(".jsonl")]] = [json.loads(line) for line in fh if line.strip()]
    summary = {}
    spath = os.path.join(out, "summary.json")
    if os.path.exists(spath):
        with open(spath, encoding="utf-8") as fh:
            summary = json.load(fh)
    return trace, state, conflicts, summary


def check(trace, state, conflicts, summary):
    """Return the list of invariant violations of a finished run."""
    problems = []
    # 1. Writes are accepted by at most one node per tick.
    per_tick = {}
    for row in trace:
        if "event" not in row and row.get("result") == "ok":
            per_tick.setdefault(row["t"], set()).add(row["node"])
    for t, names in sorted(per_tick.items()):
        if len(names) > 1:
            problems.append(f"t={t}: writes accepted by {sorted(names)}")
    # 2. At most one node holds a valid lease, and the simulator recorded the
    #    same set when it accepted writes.
    for t, names in summary.get("lease_holders", {}).items():
        if len(names) > 1:
            problems.append(f"t={t}: lease held by {sorted(names)}")
    for t, names in summary.get("accepted", {}).items():
        if len(names) > 1:
            problems.append(f"t={t}: accepted writes recorded by {sorted(names)}")
    # 3a. With no partition at the end, every live node must have converged on
    #     the same log and key/value state.
    groups = summary.get("final_partition")
    if groups is None:
        live = {name: st for name, st in state.items() if st["alive"]}
        if len({json.dumps(st["log"]) for st in live.values()}) > 1:
            problems.append("live nodes have diverging logs: " +
                            json.dumps({n: st["log"] for n, st in live.items()}, sort_keys=True))
        if len({json.dumps(st["kv"], sort_keys=True) for st in live.values()}) > 1:
            problems.append("live nodes have diverging kv: " +
                            json.dumps({n: st["kv"] for n, st in live.items()}, sort_keys=True))
    # 3b. Still partitioned: at least every node its primary can reach must
    #     have caught up; the other side legitimately lags until the heal.
    else:
        for primary in [n for n, st in state.items() if st["alive"] and st["role"] == "primary"]:
            reachable = next((set(g) for g in groups if primary in g), {primary})
            live = {n: st for n, st in state.items() if st["alive"] and n in reachable}
            if len({json.dumps(st["log"]) for st in live.values()}) > 1:
                problems.append("nodes reachable from {0} have diverging logs: ".format(primary) +
                                json.dumps({n: st["log"] for n, st in live.items()}, sort_keys=True))
    # 4. Every rollback recorded in summary.json is reflected in the node's
    #    conflict file: same entries, same indexes, contiguous per episode.  A
    #    rolled-back entry may legitimately come back at the same index (a
    #    later elected primary still carried that write), but the same term
    #    must never mean a different value -- that would be two primaries
    #    writing one log position.
    for name, entries in conflicts.items():
        st = state.get(name)
        if st is None:
            problems.append(f"conflict file for unknown node {name}")
            continue
        episodes = [r for r in summary.get("reconciliations", []) if r["node"] == name]
        expected = [dict(e) for r in episodes for e in r["rolled_back"]]
        if sorted(expected, key=lambda e: json.dumps(e, sort_keys=True)) != \
                sorted(entries, key=lambda e: json.dumps(e, sort_keys=True)):
            problems.append(f"{name}: conflict file does not match recorded rollbacks")
        for entry in entries:
            index = entry["index"]
            if index <= 0:
                problems.append(f"{name}: conflict index {index} is not a log position")
            if index <= len(st["log"]):
                term, key, value = st["log"][index - 1]
                if term == entry["term"] and (key, value) != (entry["key"], entry["value"]):
                    problems.append(f"{name}: log position {index} rewritten inside term {term}")
            if entry["term"] > st["term"]:
                problems.append(f"{name}: rolled-back term {entry['term']} newer than node term {st['term']}")
        for r in episodes:
            indexes = [e["index"] for e in r["rolled_back"]]
            if indexes != list(range(r["fork"] + 1, r["fork"] + 1 + len(indexes))):
                problems.append(f"{name}: rollback at t={r['t']} is not the suffix after the fork")
    # 5. Terms are monotone: a node never ends below a term it was promoted or
    #    stepped down to.
    terms = {name: 1 for name in state}
    for row in trace:
        if "event" in row:
            terms[row["node"]] = max(terms.get(row["node"], 1), row["term"])
    for name, st in state.items():
        if st["term"] < terms.get(name, 1):
            problems.append(f"{name}: state term {st['term']} below traced term {terms[name]}")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("scenario")
    r.add_argument("out")
    s = sub.add_parser("show")
    s.add_argument("out")
    c = sub.add_parser("check")
    c.add_argument("out")
    args = parser.parse_args()
    if args.cmd == "show":
        with open(os.path.join(args.out, "state.json"), encoding="utf-8") as fh:
            for name, st in json.load(fh).items():
                with open(os.path.join(args.out, "conflicts", f"{name}.jsonl"), encoding="utf-8") as cf:
                    rolled = [json.loads(line) for line in cf]
                print(name, st["role"], "term", st["term"], "kv", json.dumps(st["kv"], sort_keys=True),
                      "rolled back", [(x["index"], x["key"], x["value"]) for x in rolled])
        return 0
    if args.cmd == "check":
        problems = check(*load(args.out))
        for problem in problems:
            print("FAIL", problem)
        print("ok" if not problems else "{0} problem(s)".format(len(problems)))
        return 0 if not problems else 1
    with open(args.scenario, encoding="utf-8") as fh:
        spec = json.load(fh)
    cluster = Cluster(spec)
    for t in range(1, spec["ticks"] + 1):
        cluster.tick(t)
    cluster.save(args.out)
    lines = [(row["t"], i, json.dumps(row)) for i, row in enumerate(cluster.trace)
             if "event" in row or row["result"] != "ok"]
    lines += [(r["t"], len(cluster.trace) + i,
               json.dumps({"t": r["t"], "event": "reconcile", "node": r["node"], "term": r["term"],
                           "fork": r["fork"],
                           "rolled_back": [e["key"] + "=" + e["value"] for e in r["rolled_back"]]}))
              for i, r in enumerate(cluster.reconciliations)]
    for _, _, line in sorted(lines):
        print(line)
    problems = check(*load(args.out))
    for problem in problems:
        print("FAIL", problem)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
