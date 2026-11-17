#!/usr/bin/env python3
"""Replay a scripted transaction workload against the lock manager.

There are no lock timeouts: blocked transactions wait in FIFO queues and a
wait-for graph detects deadlocks when a request blocks.  The victim of a
deadlock is the cycle member with the fewest written rows (ties: latest
begin), matching the rules in the README.
"""
import json
import sys

from deadlock import DeadlockDetector
from lockmgr import LockTable


class Simulator:
    def __init__(self):
        self.locks, self.events = LockTable(), []
        # operations of each tx that have not been processed yet
        self.pending = {}
        # transactions currently blocked on a lock
        self.blocked = set()
        # whether the request a tx is blocked on is a write (counts on grant)
        self.blocked_write = {}
        self.written, self.started = {}, {}
        self.committed, self.aborted = [], []
        self.detector = DeadlockDetector(self.locks)

    def log(self, **event):
        self.events.append(event)

    def submit(self, op):
        tx = op["tx"]
        if tx in self.aborted or tx in self.committed:
            return
        self.pending.setdefault(tx, []).append(op)
        if tx not in self.blocked:
            self.drain(tx)

    def drain(self, tx):
        while self.pending.get(tx) and tx not in self.blocked:
            op = self.pending[tx].pop(0)
            kind = op["op"]
            if kind == "begin":
                self.started[tx] = len(self.started)
            elif kind in ("lock", "write"):
                self.acquire(tx, op, kind == "write")
            elif kind in ("commit", "abort"):
                (self.committed if kind == "commit" else self.aborted).append(tx)
                self.log(event=kind, tx=tx)
                self.finish(tx)

    def acquire(self, tx, op, is_write):
        mode = op.get("mode", "X")
        granted_mode = self.locks.request(tx, op["res"], mode)
        if granted_mode is not None:
            self.log(event="granted", tx=tx, res=op["res"], mode=granted_mode)
            if is_write:
                self.written[tx] = self.written.get(tx, 0) + 1
            return
        # The request blocked: record the wait and maintain the graph.
        effective = self.locks.queued_mode(tx) or mode
        self.blocked_write[tx] = is_write
        self.detector.add_waiter(tx)
        self.blocked.add(tx)
        self.log(event="waiting", tx=tx, res=op["res"], mode=effective)
        self.resolve_deadlocks(tx)

    def resolve_deadlocks(self, tx):
        # Abort victims while the blocked requester is still blocked and on a
        # cycle.  Once it has been woken (or aborted) the resolution is done:
        # any unrelated cycle left behind is handled when one of its members
        # next blocks, never by an extra speculative abort.
        while tx in self.blocked and self.locks.is_waiting(tx):
            cycle = self.detector.find_cycle(tx)
            if cycle is None:
                return
            victim = self.detector.pick_victim(cycle, self.written, self.started)
            self.log(event="deadlock", cycle=sorted(cycle), victim=victim)
            self.abort_tx(victim)

    def abort_tx(self, tx):
        if tx in self.aborted:
            return
        self.aborted.append(tx)
        self.log(event="abort", tx=tx)
        self.pending.pop(tx, None)
        self.finish(tx)

    def finish(self, tx):
        self.blocked.discard(tx)
        self.blocked_write.pop(tx, None)
        self.detector.remove(tx)
        woken, touched = self.locks.release_all(tx)
        self.detector.refresh_resources(touched)
        for t, res, mode in woken:
            self.log(event="granted", tx=t, res=res, mode=mode)
            self.detector.remove(t)
            self.blocked.discard(t)
            if self.blocked_write.pop(t, False):
                self.written[t] = self.written.get(t, 0) + 1
        # Woken transactions resume in the order in which they were granted.
        for t in dict.fromkeys(t for t, _, _ in woken):
            if t not in self.blocked and t not in self.aborted and t not in self.committed:
                self.drain(t)


def run(script):
    sim = Simulator()
    for op in script["ops"]:
        sim.submit(op)
    return {"events": sim.events, "committed": sim.committed, "aborted": sim.aborted,
            "waiting": sorted(sim.blocked)}


def report(out):
    for e in out["events"]:
        rest = " ".join(f"{k}={v}" for k, v in e.items() if k not in ("event", "tx"))
        print(f"{e['event']:<9} {e.get('tx', ''):>3} {rest}")
    print("committed:", out["committed"], " aborted:", out["aborted"], " still waiting:", out["waiting"])


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("run", "report"):
        raise SystemExit("usage: locksim.py run|report <script.json>")
    with open(sys.argv[2], encoding="utf-8") as fh:
        out = run(json.load(fh))
    report(out) if sys.argv[1] == "report" else print(json.dumps(out))
