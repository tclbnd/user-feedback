#!/usr/bin/env python3
"""Multi-stage pipeline simulation with per-level backpressure and a
waterline protocol.

CLI (unchanged):  python3 pipeline.py scenario.json output.json

Semantics per tick:
  1. Source generates up to `concurrency` new items (one epoch per tick) into
     a pending pool, then admits them into stage-0's buffer ordered by
     (epoch, priority, id): same-epoch data is never interleaved across
     epochs, and within an epoch higher priority is admitted first.
     If the buffer is at its waterline (cap), the remainder is rejected per
     `reject_policy`:
       - "block" (default): deferred to the next tick, metered as
         blocked_ticks of the source -- no data is ever lost;
       - "drop":  discarded, metered in dropped / drops_by_stage.
  2. Each stage (upstream -> downstream) moves at most 1 item to the next
     stage's input buffer. If that buffer is full, the item is HELD and the
     stage raises a backpressure signal (blocked_ticks += 1); the signal thus
     propagates level by level. A stalled stage simply does not consume;
     stages downstream of it keep draining (a slow consumer never blocks
     queues that do not depend on it). The sink always consumes unless
     stalled, so recovered stages drain in FIFO order and catch up in order.
  3. Water levels are sampled. If a whole tick makes no progress while work
     remains and no stall is active -> deadlock (cannot happen in this
     design while the sink can drain, so injection scenarios never
     false-positive).
"""
import json, sys
from collections import deque

MAX_TICKS = 10000

def run(sc):
    stages = sc["stages"]
    names = [s["name"] for s in stages]
    caps = [s["cap"] for s in stages]
    n_items = sc["items"]
    concurrency = sc.get("concurrency", 1)
    policy = sc.get("reject_policy", "block")   # "block" | "drop"
    prio_list = sc.get("priorities")            # optional per-id priority
    stall = sc.get("stall")

    q = [deque() for _ in stages]               # q[i] = input buffer of stage i
    pending = []                                # generated, not yet admitted to q[0]
    generated = 0
    admitted = []                               # ids in admission order (reference order)
    done, done_epochs = [], []
    tick = 0
    levels = {n: [] for n in names}
    blocked = {n: 0 for n in names}
    dropped = 0
    drops_by_stage = {n: 0 for n in names}
    deadlock = False

    def prio(i):
        return prio_list[i] if prio_list else 0

    while len(done) < n_items and tick < MAX_TICKS:
        progress = False
        stalled_now = set()
        if stall:
            for i, s in enumerate(stages):
                if s["name"] == stall["stage"] and stall["from"] <= tick < stall["to"]:
                    stalled_now.add(i)

        # 1) source: generate this tick's epoch, admit by (epoch, prio, id)
        for _ in range(concurrency):
            if generated < n_items:
                pending.append((generated, tick, prio(generated)))
                generated += 1
        if pending:
            pending.sort(key=lambda it: (it[1], it[2], it[0]))
            while pending and len(q[0]) < caps[0]:
                item = pending.pop(0)
                q[0].append(item)
                admitted.append(item[0])
                progress = True
            if pending:
                # rejected at the waterline: backpressure or metered drop
                if policy == "drop":
                    drops_by_stage[names[0]] += len(pending)
                    dropped += len(pending)
                    pending.clear()
                else:
                    blocked[names[0]] += 1

        # 2) drain stages upstream -> downstream; full downstream => hold + signal
        for i in range(len(stages)):
            if i in stalled_now or not q[i]:
                continue
            if i + 1 < len(stages) and len(q[i+1]) >= caps[i+1]:
                blocked[names[i]] += 1      # backpressure signal to level i
                continue
            item = q[i].popleft()
            progress = True
            if i + 1 == len(stages):
                done.append(item[0])
                done_epochs.append(item[1])
            else:
                q[i+1].append(item)

        # 3) waterline sampling, termination, deadlock detection
        for i, s in enumerate(stages):
            levels[s["name"]].append(len(q[i]))
        if generated >= n_items and not pending and not any(q):
            break                           # nothing left in flight
        work_left = generated < n_items or pending or any(q)
        if not progress and work_left and not stalled_now:
            deadlock = True                 # everyone blocked, no stall
            break
        tick += 1

    high_water = {n: (max(levels[n]) if levels[n] else 0) for n in names}
    order_ok = done == admitted[:len(done)]     # pipeline never reorders
    epoch_ok = all(done_epochs[i] <= done_epochs[i + 1]
                   for i in range(len(done_epochs) - 1))
    return {
        "dropped": dropped,
        "drops_by_stage": drops_by_stage,
        "deadlock": deadlock,
        "water_levels": levels,
        "high_water": high_water,
        "blocked_ticks": blocked,
        "throughput": len(done),
        "order_ok": order_ok,
        "epoch_ok": epoch_ok,
        "ticks": tick,
    }

def main():
    sc = json.load(open(sys.argv[1]))
    json.dump(run(sc), open(sys.argv[2], "w"))

if __name__ == "__main__":
    main()
