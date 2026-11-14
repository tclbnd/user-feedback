#!/usr/bin/env python3
"""Backpressure pipeline simulation (drops-on-full baseline)."""
import json, sys
from collections import deque

def run(sc):
    stages = sc["stages"]
    q = [deque() for _ in stages]            # q[i] = input buffer of stage i
    nxt = 0; done = []; tick = 0
    levels = {s["name"]: [] for s in stages}
    blocked = {s["name"]: 0 for s in stages}
    dropped = 0
    stall = sc.get("stall")
    while len(done) < sc["items"] and tick < 10000:
        # inject
        for _ in range(sc.get("concurrency", 1)):
            if nxt < sc["items"]:
                if len(q[0]) < stages[0]["cap"]:
                    q[0].append(nxt); nxt += 1
                else:
                    dropped += 1; nxt += 1   # BUG: drop on full
        # drain each stage
        for i, s in enumerate(stages):
            stalled = stall and s["name"] == stall["stage"] and stall["from"] <= tick < stall["to"]
            if stalled or not q[i]:
                continue
            item = q[i].popleft()
            if i + 1 == len(stages):
                done.append(item)
            else:
                if len(q[i+1]) < stages[i+1]["cap"]:
                    q[i+1].append(item)    # BUG: no upstream blocking order guarantee
                else:
                    dropped += 1           # BUG: drop instead of block
        for i, s in enumerate(stages):
            levels[s["name"]].append(len(q[i]))
            if not q[i]: pass
        tick += 1
    return {"dropped": dropped, "deadlock": False, "water_levels": levels,
            "blocked_ticks": blocked, "throughput": len(done),
            "order_ok": done == sorted(done), "ticks": tick}

def main():
    sc = json.load(open(sys.argv[1]))
    json.dump(run(sc), open(sys.argv[2], "w"))

if __name__ == "__main__":
    main()
