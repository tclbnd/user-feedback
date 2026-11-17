#!/usr/bin/env python3
"""Replay a scripted transaction workload against the lock manager."""
import json
import sys

from deadlock import Watchdog
from lockmgr import LockTable


class Simulator:
    def __init__(self):
        self.locks, self.events = LockTable(), []
        self.pending, self.blocked, self.written, self.started = {}, set(), {}, {}
        self.committed, self.aborted = [], []
        self.watchdog = Watchdog()

    def log(self, **event):
        self.events.append(event)

    def submit(self, op):
        self.watchdog.tick()
        self.expire()
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
                mode = op.get("mode", "X")
                if self.locks.request(tx, op["res"], mode):
                    self.log(event="granted", tx=tx, res=op["res"], mode=mode)
                    self.watchdog.granted()
                    if kind == "write":
                        self.written[tx] = self.written.get(tx, 0) + 1
                else:
                    if kind == "write":
                        self.pending[tx].insert(0, {"op": "count", "tx": tx})
                    self.blocked.add(tx)
                    self.log(event="waiting", tx=tx, res=op["res"], mode=mode)
                    self.watchdog.waiting(tx)
            elif kind == "count":
                self.written[tx] = self.written.get(tx, 0) + 1
            elif kind in ("commit", "abort"):
                (self.committed if kind == "commit" else self.aborted).append(tx)
                self.log(event=kind, tx=tx)
                self.finish(tx)

    def expire(self):
        victim = self.watchdog.expired()
        if victim is None:
            return
        self.log(event="timeout", tx=victim)
        self.aborted.append(victim)
        self.log(event="abort", tx=victim)
        self.pending.pop(victim, None)
        self.finish(victim)

    def finish(self, tx):
        self.blocked.discard(tx)
        self.watchdog.resumed(tx)
        woken = self.locks.release_all(tx)
        for t, res, mode in woken:
            self.log(event="granted", tx=t, res=res, mode=mode)
            self.watchdog.granted()
            self.watchdog.resumed(t)
            self.blocked.discard(t)
        for t in dict.fromkeys(t for t, _, _ in woken):
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
