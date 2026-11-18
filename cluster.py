"""Simulate a sharded cluster and inject crashes.

    python3 cluster.py scenario.json result.json

Scenario:

    {"shards": {"A": {"alice": 100}, "B": {"bob": 100}},
     "placement": {"alice": "A", "bob": "B"},
     "steps": [{"op": "transfer", "tx": "t1", "from": "alice", "to": "bob", "amount": 30,
                "crash": "after_prepare"},
               {"op": "restart_coordinator"},
               {"op": "recover"}]}
"""
import json
import sys

from xshard.coordinator import Coordinator, Crash
from xshard.participant import Down, LockConflict, Participant
from xshard.transfers import balance_report, transfer_writes


def run(spec):
    shards = {name: Participant(name, bal) for name, bal in spec["shards"].items()}
    coord = Coordinator(shards)
    placement = spec.get("placement", {})
    events = []
    for step in spec["steps"]:
        op = step["op"]
        if op == "transfer":
            writes = transfer_writes(step["from"], step["to"], step["amount"], shards, placement)
            try:
                events.append({"tx": step["tx"], "decision": coord.run(step["tx"], writes, step.get("crash"))})
            except Crash:
                events.append({"tx": step["tx"], "decision": "coordinator crashed"})
                coord.crash()
        elif op == "restart_coordinator":
            coord.crash()
        elif op == "crash_shard":
            shards[step["shard"]].crash()
        elif op == "restart_shard":
            shards[step["shard"]].restart()
        elif op == "recover":
            try:
                coord.recover()
            except Crash:
                # A fault injection can also kill the coordinator mid-recovery;
                # treat it like any other crash so later restart/recover steps
                # can drive the cluster to convergence.
                events.append({"recover": "coordinator crashed"})
                coord.crash()
            for shard in shards.values():
                try:
                    shard.resolve(coord)
                except Down:
                    pass
        elif op == "write":
            try:
                shards[step["shard"]].write(step["key"], step["delta"])
                events.append({"write": step["key"], "ok": True})
            except (LockConflict, Down) as exc:
                events.append({"write": step["key"], "ok": False, "error": str(exc)})
        else:
            raise ValueError(f"unknown op {op}")

    txids = [s["tx"] for s in spec["steps"] if s["op"] == "transfer"]
    return {
        "events": events,
        "outcomes": {tx: {n: s.outcome(tx) for n, s in sorted(shards.items())} for tx in txids},
        "locks": {n: dict(s.locks) for n, s in sorted(shards.items())},
        "report": balance_report(shards),
    }


def main():
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(run(spec), f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
