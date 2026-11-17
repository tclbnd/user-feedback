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

    def grantable(self, request):
        mode, nid = request
        held = self.latches.get(nid)
        return mode == "U" or not held or (mode == "S" and held[0] == "S")

    def apply(self, tid, request):
        mode, nid = request
        if mode == "U":
            held = self.latches[nid]
            held[1].discard(tid)
            if not held[1]:
                del self.latches[nid]
        else:
            self.latches.setdefault(nid, (mode, set()))[1].add(tid)

    def run(self, threads):
        pending = {}
        for tid, gen in threads.items():
            pending[tid] = next(gen, None)
        pending = {t: r for t, r in pending.items() if r is not None}
        steps = 0
        while pending:
            ready = sorted(t for t, r in pending.items() if self.grantable(r))
            if not ready:
                return {"deadlock": True, "waiting": {str(t): list(r) for t, r in pending.items()}}
            tid = self.rng.choice(ready)
            self.apply(tid, pending[tid])
            try:
                pending[tid] = threads[tid].send(None)
            except StopIteration:
                del pending[tid]
            steps += 1
        return {"deadlock": False, "steps": steps}


def program(tree, script, results):
    for op in script:
        if op["op"] == "insert":
            yield from ops.insert(tree, op["key"])
            results.append({"ok": True})
        elif op["op"] == "delete":
            yield from ops.delete(tree, op["key"])
            results.append({"ok": True})
        else:
            keys = yield from ops.scan(tree, op["lo"], op["hi"])
            results.append({"keys": keys})


def run(workload, seed):
    tree = Tree()
    Scheduler(0).run({0: program(tree, [{"op": "insert", "key": k} for k in workload.get("initial", [])], [])})
    results = [[] for _ in workload["threads"]]
    threads = {i: program(tree, s, results[i]) for i, s in enumerate(workload["threads"])}
    status = Scheduler(seed).run(threads)
    return {**status, "results": results, "tree": tree.dump()}


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
