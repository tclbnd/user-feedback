#!/usr/bin/env python3
"""Simulate a primary/replica cluster under partitions, crashes and clock drift."""
import argparse
import json
import os
import sys
from collections import Counter

from controller import Controller
from node import Node


class ScenarioError(ValueError):
    """Raised for a scenario the safety model cannot be applied to."""


def validate(spec):
    """Reject scenarios that would silently void the safety argument."""
    names = spec.get("nodes") or []
    if not names:
        raise ScenarioError("scenario needs at least one node")
    if len(set(names)) != len(names):
        raise ScenarioError("node names must be unique")
    lease = spec.get("lease")
    if not isinstance(lease, (int, float)) or lease <= 0:
        raise ScenarioError("lease must be a positive number")
    drift = spec.get("drift", 0.0)
    if not 0 <= drift < 1:
        raise ScenarioError("drift must lie in [0, 1)")
    ticks = spec.get("ticks")
    if not isinstance(ticks, int) or isinstance(ticks, bool) or ticks < 0:
        raise ScenarioError("ticks must be a non-negative integer")
    rates = spec.get("clock_rate", {})
    for name, rate in rates.items():
        if name not in names:
            raise ScenarioError(f"clock_rate names unknown node {name!r}")
        # The lease margin is derived from this bound; a wider rate could let
        # an old primary write after its replacement was elected.
        if not 1 - drift <= rate <= 1 + drift:
            raise ScenarioError(
                f"clock_rate[{name!r}] = {rate} outside [1 - drift, 1 + drift]; "
                "the lease safety margin assumes bounded clock drift")
    for ev in spec.get("events", []):
        if not isinstance(ev.get("t"), int) or not 1 <= ev["t"] <= ticks:
            raise ScenarioError(f"event {ev} has t outside 1..ticks")
        if "partition" in ev:
            groups = ev["partition"]
            if not groups:
                raise ScenarioError("partition needs at least one group")
            members = [m for group in groups for m in group]
            unknown = [m for m in members if m != "ctl" and m not in names]
            if unknown:
                raise ScenarioError(f"partition names unknown members {unknown}")
        for kind in ("crash", "restart"):
            if kind in ev and ev[kind] not in names:
                raise ScenarioError(f"{kind} names unknown node {ev[kind]!r}")
    for w in spec.get("writes", []):
        if not isinstance(w.get("t"), int) or not 1 <= w["t"] <= ticks:
            raise ScenarioError(f"write {w} has t outside 1..ticks")
        if w.get("node") not in names:
            raise ScenarioError(f"write {w} names unknown node {w.get('node')!r}")
        if "key" not in w or "value" not in w:
            raise ScenarioError(f"write {w} needs key and value")
    return spec


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
        # tick -> names that could accept a write during that tick.  Recorded
        # for every node that is writable, so audit() can prove at most one
        # writer per tick even on ticks with no client writes.
        self.accepted = {}

    def reach(self, a, b):
        names = {getattr(a, "name", a), getattr(b, "name", b)}
        if any(isinstance(x, Node) and not x.alive for x in (a, b)):
            return False
        return self.groups is None or any(names <= set(g) for g in self.groups)

    def node(self, name):
        return next(n for n in self.nodes if n.name == name)

    def push(self, t, leader):
        """Replicate `leader`'s log to every node it reaches.

        A follower whose term is newer refuses the push; the stale leader is
        then fenced, i.e. it steps down and adopts the newer term.  Returns
        False in that case so the caller can report the write as `fenced`.
        """
        for other in self.nodes:
            if other is leader or not self.reach(leader, other):
                continue
            if not other.receive(leader):
                leader.step_down(other.term)
                self.trace.append({"t": t, "event": "step_down",
                                   "node": leader.name, "term": leader.term})
                return False
            detail = other.last_push
            if detail["rolled_back"]:
                # The rejoining node found its fork point and exported the
                # divergent suffix; record it so the rollback is auditable.
                self.trace.append({"t": t, "event": "rollback", "node": other.name,
                                   "leader": leader.name, "term": other.term,
                                   "fork": detail["fork"],
                                   "rolled_back": detail["rolled_back"]})
        return True

    def tick(self, t):
        for ev in self.spec.get("events", []):
            if ev["t"] != t:
                continue
            if "partition" in ev:
                self.groups = ev["partition"]
            if ev.get("heal"):
                self.groups = None
            if "crash" in ev:
                self.node(ev["crash"]).crash()
            if "restart" in ev:
                self.node(ev["restart"]).restart()
        for n in self.nodes:
            if n.alive and n.role == "primary" and self.reach(n, "ctl") and not self.ctl.renew(n, t):
                n.step_down(self.ctl.term)
                self.trace.append({"t": t, "event": "step_down", "node": n.name, "term": n.term})
        winner = self.ctl.failover(t, [n for n in self.nodes if self.reach(n, "ctl")])
        if winner:
            self.trace.append({"t": t, "event": "promote", "node": winner.name, "term": winner.term})
        # Record who *could* accept writes this tick, not just who did.  The
        # single-writer check in audit() then holds on write-free ticks too,
        # which matters during a partition where both sides sit idle.
        writable = {n.name for n in self.nodes if n.writable(t, self.lease, self.drift)}
        if writable:
            self.accepted.setdefault(t, set()).update(writable)
        for w in self.spec.get("writes", []):
            if w["t"] != t:
                continue
            n, result = self.node(w["node"]), "rejected"
            # Capture the term before the write: if the push below fences this
            # primary, the write must be reported `fenced`, not `ok`.
            term = n.term
            if n.writable(t, self.lease, self.drift):
                if n.store_write(term, w["key"], w["value"]):
                    result = "ok" if self.push(t, n) else "fenced"
                else:
                    result = "fenced"  # storage refused a stale term
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

    def audit(self):
        """Check the safety invariants and the fidelity of the rollback records."""
        def ident(entry):
            return (entry["term"], entry["key"], entry["value"])
        report = {"single_writer": True, "consistent": True, "primary": None,
                  "violations": [], "writes": {}, "rollbacks": []}
        for t, names in sorted(self.accepted.items()):
            if len(names) > 1:
                report["single_writer"] = False
                report["violations"].append(
                    {"t": t, "reason": "multiple writers in one tick", "nodes": sorted(names)})
        for row in self.trace:
            if "key" not in row:
                continue
            slot = report["writes"].setdefault(row["node"], {"ok": 0, "rejected": 0, "fenced": 0})
            slot[row["result"]] += 1

        primary = [n for n in self.nodes if n.role == "primary"]
        report["primary"] = primary[0].name if len(primary) == 1 else None
        report["consistent"] = len(primary) == 1 and all(
            n.log == primary[0].log for n in self.nodes if n.alive)

        # The conflict file must be exactly the concatenation of what each
        # rollback event truncated, in order, with the original indexes.
        for n in self.nodes:
            events = [r for r in self.trace
                      if r.get("event") == "rollback" and r["node"] == n.name]
            expected = [(e["index"], e["term"], e["key"], e["value"])
                        for ev in events for e in ev["rolled_back"]]
            actual = [(c["index"], c["term"], c["key"], c["value"]) for c in n.conflicts]
            if expected != actual:
                report["violations"].append(
                    {"node": n.name, "reason": "conflict file does not match rollback events",
                     "expected": expected, "actual": actual})
            report["rollbacks"].extend(
                {"t": ev["t"], "node": n.name, "leader": ev["leader"],
                 "fork": ev["fork"], "entries": ev["rolled_back"]} for ev in events)
            worst = max((e[0] for e in expected), default=0)
            if worst > len(n.history):
                report["violations"].append(
                    {"node": n.name, "reason": "rolled-back index beyond written history"})

            # No silent loss: anything this node ever appended but no longer
            # has must have been exported.  A rolled-back entry may reappear
            # later (another replica that had copied it can win an election),
            # which is legitimate and simply means it is not "lost" now.
            lost = Counter(ident(e) for e in n.history) - Counter(ident(e) for e in n.log)
            rolled = Counter((c["term"], c["key"], c["value"]) for c in n.conflicts)
            silent = lost - rolled
            if silent:
                report["violations"].append(
                    {"node": n.name, "reason": "entry lost without a rollback record",
                     "entries": [{"term": term, "key": key, "value": value}
                                 for (term, key, value) in sorted(silent.elements())]})
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("scenario")
    r.add_argument("out")
    s = sub.add_parser("show")
    s.add_argument("out")
    v = sub.add_parser("verify")
    v.add_argument("scenario")
    args = parser.parse_args()
    if args.cmd == "show":
        with open(os.path.join(args.out, "state.json"), encoding="utf-8") as fh:
            state = json.load(fh)
        for name, st in state.items():
            with open(os.path.join(args.out, "conflicts", f"{name}.jsonl"), encoding="utf-8") as c:
                rolled = [json.loads(line) for line in c]
            print(name, st["role"], "term", st["term"], "log",
                  [(e[0], e[1], e[2]) for e in st["log"]],
                  "kv", json.dumps(st["kv"], sort_keys=True),
                  "rolled back", [(c["index"], c["key"], c["value"]) for c in rolled])
        return 0
    with open(args.scenario, encoding="utf-8") as fh:
        spec = json.load(fh)
    try:
        validate(spec)
    except ScenarioError as exc:
        print(f"invalid scenario: {exc}", file=sys.stderr)
        return 2
    cluster = Cluster(spec)
    for t in range(1, spec["ticks"] + 1):
        cluster.tick(t)
    if args.cmd == "run":
        cluster.save(args.out)
    for row in cluster.trace:
        if "event" in row or row["result"] != "ok":
            print(json.dumps(row))
    if args.cmd == "verify":
        report = cluster.audit()
        ok = report["single_writer"] and report["consistent"] and not report["violations"]
        print(json.dumps({"single_writer": report["single_writer"],
                          "consistent": report["consistent"],
                          "primary": report["primary"],
                          "violations": report["violations"],
                          "rollbacks": report["rollbacks"],
                          "writes": report["writes"]}, sort_keys=True))
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
