#!/usr/bin/env python3
"""Run interleaved B+ tree workloads under a deterministic scheduler."""
import argparse
import json
import random

import ops
from tree import Tree


class Scheduler:
    def __init__(self, seed):
        self.rng, self.latches = random.Random(seed), {}
        # Per-thread set of latched node ids, so a re-entrant request by the
        # same thread is recognised instead of deadlocking against itself.
        self.held_by = {}

    def grantable(self, tid, request):
        mode, nid = request
        if nid in self.held_by.get(tid, ()):
            return True  # already held by this thread: re-entrant no-op
        held = self.latches.get(nid)
        return mode == "U" or not held or (mode == "S" and held[0] == "S")

    def apply(self, tid, request):
        mode, nid = request
        mine = self.held_by.setdefault(tid, set())
        if mode == "U":
            held = self.latches[nid]
            held[1].discard(tid)
            mine.discard(nid)
            if not held[1]:
                del self.latches[nid]
        else:
            if nid in mine:
                return  # re-entrant acquire: keep the single registration
            self.latches.setdefault(nid, (mode, set()))[1].add(tid)
            mine.add(nid)

    def release_all(self, tid):
        for nid in list(self.held_by.get(tid, ())):
            held = self.latches.get(nid)
            if held:
                held[1].discard(tid)
                if not held[1]:
                    del self.latches[nid]
        self.held_by.pop(tid, None)

    def _wait_graph(self, pending):
        """Edge holder-tid -> waiting-tid for every currently blocked tid."""
        edges = {}
        for tid, request in pending.items():
            mode, nid = request
            if nid in self.held_by.get(tid, ()):
                continue  # re-entrant request, never blocks
            held = self.latches.get(nid)
            if held and not (mode == "S" and held[0] == "S"):
                for owner in held[1]:
                    if owner != tid:
                        edges.setdefault(owner, set()).add(tid)
        return edges

    @staticmethod
    def _cycle_members(edges):
        """Return the tids that lie on a directed cycle, if any."""
        on_cycle = set()
        for start in edges:
            stack = [(start, iter(edges.get(start, ())))]
            path = [start]
            on_path = {start}
            while stack:
                node, it = stack[-1]
                nxt = next(it, None)
                if nxt is None:
                    stack.pop()
                    on_path.discard(path.pop())
                    continue
                if nxt == start and len(path) > 1:
                    on_cycle.update(path)
                elif nxt not in on_path:
                    path.append(nxt)
                    on_path.add(nxt)
                    stack.append((nxt, iter(edges.get(nxt, ()))))
        return on_cycle

    def run(self, runners, max_idle=10000):
        """Drive runner generators, restarting the operations on a wait cycle.

        A runner yields latch requests like a plain thread.  When no request
        is grantable and the wait graph contains a cycle, every runner on
        that cycle is aborted (its latches released) and its current
        operation is replayed from the start.  The latch order used by
        ``ops`` makes cycles rare; this is the safety net that makes the
        system deadlock-free rather than merely deadlock-prone.  A blocked
        configuration without a cycle is a queue, not a deadlock; more than
        ``max_idle`` consecutive idle ticks abort with a liveness failure."""
        pending = {}
        for tid, runner in runners.items():
            pending[tid] = next(runner, None)
        pending = {t: r for t, r in pending.items() if r is not None}
        steps, restarts, idle = 0, 0, 0
        while pending:
            ready = sorted(t for t, r in pending.items() if self.grantable(t, r))
            if not ready:
                victims = self._cycle_members(self._wait_graph(pending))
                if victims:
                    for tid in victims:
                        self.release_all(tid)
                        try:
                            pending[tid] = runners[tid].throw(RetryOperation)
                        except StopIteration:
                            del pending[tid]
                    restarts += 1
                    idle = 0
                    continue
                idle += 1
                if idle > max_idle:
                    return {"deadlock": True, "steps": steps, "restarts": restarts,
                            "waiting": {str(t): list(r) for t, r in pending.items()}}
                # Keep the deterministic stream advancing while the world is
                # blocked, so the grant that unblocks the queue is observed.
                self.rng.random()
                continue
            idle = 0
            tid = self.rng.choice(ready)
            self.apply(tid, pending[tid])
            try:
                pending[tid] = runners[tid].send(None)
            except StopIteration:
                del pending[tid]
            steps += 1
        return {"deadlock": False, "steps": steps, "restarts": restarts}


class RetryOperation(Exception):
    """Raised into a runner to abort and replay its current operation."""


class Trace:
    """Scan windows for invariant checks.  Committed key sets live on the
    tree itself (``tree.snapshots``), one immutable frozenset per version."""

    def __init__(self, tree):
        self.tree = tree
        self.scans = []

    def scan_start(self, lo, hi):
        rec = {"lo": lo, "hi": hi, "version": None, "keys": None}
        self.scans.append(rec)
        return rec  # opaque handle; never index the shared list later

    def scan_end(self, rec, keys, read_version):
        rec["version"] = read_version
        rec["keys"] = list(keys)

    def export(self):
        return {"snapshots": [[v, sorted(keys)]
                              for v, keys in sorted(self.tree.snapshots.items())],
                "scans": self.scans}


def runner(tree, script, results, trace=None):
    """One thread.  Each operation is a replayable unit.

    Replay happens when the scheduler breaks a wait cycle by throwing
    ``RetryOperation``.  The aborted operation had not committed, so replaying
    it simply re-reads the current tree.  A scan pins an immutable snapshot
    and therefore never needs to retry for consistency.  Results are appended
    only on completion."""
    for op in script:
        while True:
            scan_rec = None
            try:
                if op["op"] == "insert":
                    yield from ops.insert(tree, op["key"])
                    tree.commit()
                    results.append({"ok": True})
                elif op["op"] == "delete":
                    yield from ops.delete(tree, op["key"])
                    tree.commit()
                    results.append({"ok": True})
                else:
                    if trace is not None:
                        scan_rec = trace.scan_start(op["lo"], op["hi"])
                    keys, read_version = yield from ops.scan(tree, op["lo"], op["hi"])
                    if trace is not None:
                        trace.scan_end(scan_rec, keys, read_version)
                    results.append({"keys": keys})
                break
            except RetryOperation:
                # Remove only this thread's own open scan record.
                if trace is not None and scan_rec is not None and scan_rec["version"] is None:
                    trace.scans.remove(scan_rec)
                continue


def run_traced(workload, seed):
    tree = Tree()
    trace = Trace(tree)
    Scheduler(0).run({0: runner(
        tree, [{"op": "insert", "key": k} for k in workload.get("initial", [])], [], trace)})
    # The initial load left a committed snapshot at tree.version via commit().
    results = [[] for _ in workload["threads"]]
    runners = {i: runner(tree, s, results[i], trace) for i, s in enumerate(workload["threads"])}
    status = Scheduler(seed).run(runners)
    outcome = {**status, "results": results, "tree": tree.dump()}
    return outcome, trace


def run(workload, seed):
    outcome, _ = run_traced(workload, seed)
    # Preserve the documented output schema.
    return {k: v for k, v in outcome.items() if k != "restarts"}


def show(out):
    print("deadlock" if out["deadlock"] else f"finished in {out['steps']} steps")
    for t, res in enumerate(out["results"]):
        for r in res:
            if "keys" in r:
                print(f"thread {t} scan -> {len(r['keys'])} keys {r['keys']}")
    nodes, level = out["tree"]["nodes"], [out["tree"]["root"]]
    while level:
        print("  ".join(str(nodes[str(n)]["keys"]) for n in level))
        level = [c for n in level for c in nodes[str(n)]["children"]]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run", "show"])
    parser.add_argument("workload")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    with open(args.workload, encoding="utf-8") as fh:
        out = run(json.load(fh), args.seed)
    show(out) if args.command == "show" else print(json.dumps(out))
