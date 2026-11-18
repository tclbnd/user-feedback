"""Build an index while a write workload runs.

    python3 runbuild.py scenario.json result.json

scenario.json:

    {"rows": 200, "mode": "online",
     "writes": [{"tick": 2, "op": "insert", "key": 9001, "val": "n1", "latency": 3},
                {"tick": 4, "op": "update", "key": 17, "new_key": 9017, "val": "u17"},
                {"tick": 5, "op": "delete", "key": 30}]}

Initial rows have key i and val "r<i>" for i in 1..rows.  A write that finds
the table locked retries every tick.  The result compares an index lookup
with a full scan for every key that exists or ever existed.
"""
import json
import sys

from oidx.bloat import bloat
from oidx.build import build_offline, build_online
from oidx.heap import Heap
from oidx.server import Server


def run(spec):
    heap = Heap()
    for i in range(1, spec["rows"] + 1):
        heap.insert(i, f"r{i}")
    server = Server(heap)
    if spec.get("mode", "online") == "online":
        builder = build_online(heap, server)
    else:
        builder = build_offline(heap, server)
    pending = sorted(spec.get("writes", []), key=lambda w: w["tick"])
    index, done, tick, built_at = None, False, 0, None
    while not done or pending or server.inflight:
        if not done:
            try:
                index = next(builder)
            except StopIteration:
                done, built_at = True, tick
        waiting = []
        for w in pending:
            if w["tick"] <= tick:
                if not server.submit(w, tick):
                    server.blocked_ticks += 1
                    waiting.append(w)
            else:
                waiting.append(w)
        pending = waiting
        server.apply_due(tick)
        tick += 1
        if tick > 10_000:
            raise RuntimeError("workload did not finish")

    keys = set(t["key"] for t in heap.tuples.values()) | {w.get("new_key") for w in spec.get("writes", []) if w.get("new_key")}
    mismatches = []
    for key in sorted(keys):
        via_index = index.lookup(key)
        via_scan = heap.seq_scan(lambda t, k=key: t["key"] == k)
        if via_index != via_scan:
            mismatches.append({"key": key, "index": via_index, "scan": via_scan})
    return {"state": index.state, "built_at": built_at, "ticks": tick, "blocked_ticks": server.blocked_ticks,
            "mismatches": mismatches, "bloat": bloat(index)}


def main():
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(run(spec), f, indent=1)


if __name__ == "__main__":
    main()
