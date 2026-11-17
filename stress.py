"""Large randomized stress run: many seeds, dense mixed workloads, checking
deadlock-freedom, structural invariants and snapshot-consistent scans."""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bptree import run_traced  # noqa: E402
from tests.test_concurrency import check_invariants, check_scans  # noqa: E404


def main():
    rng = random.Random(2026)
    runs = 0
    max_restarts = 0
    for trial in range(60):
        universe = list(range(rng.randint(50, 300)))
        present = set(rng.sample(universe, rng.randint(3, len(universe))))
        nthreads = rng.randint(2, 6)
        scripts = [[] for _ in range(nthreads)]
        for _ in range(rng.randint(20, 80)):
            tid = rng.randrange(nthreads)
            roll = rng.random()
            if roll < 0.4:
                scripts[tid].append({"op": "insert", "key": rng.choice(universe)})
            elif roll < 0.75:
                scripts[tid].append({"op": "delete", "key": rng.choice(universe)})
            else:
                a, b = sorted(rng.sample(universe, 2))
                scripts[tid].append({"op": "scan", "lo": a, "hi": b})
        wl = {"initial": sorted(present), "threads": scripts}
        for seed in (trial, trial + 777):
            outcome, trace = run_traced(wl, seed)
            assert not outcome["deadlock"], (trial, seed, outcome.get("waiting"))
            check_invariants(outcome)
            check_scans(trace)
            max_restarts = max(max_restarts, outcome.get("restarts", 0))
            runs += 1
    print(f"OK: {runs} runs, max scheduler restarts observed: {max_restarts}")


if __name__ == "__main__":
    main()
