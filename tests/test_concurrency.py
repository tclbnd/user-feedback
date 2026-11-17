import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bptree import run_traced  # noqa: E402
from tree import MAX_KEYS, MIN_KEYS  # noqa: E402


def check_invariants(outcome, expected=None):
    """Structural B+ tree invariants over the reachable nodes."""
    tree = outcome["tree"]
    root, nodes = tree["root"], tree["nodes"]
    assert str(root) in nodes

    reachable, level = set(), [root]
    depth_of_leaf = {}
    depth = 0
    while level:
        nxt = []
        for nid in level:
            assert nid not in reachable, "node reached twice"
            reachable.add(nid)
            n = nodes[str(nid)]
            assert len(n["keys"]) == len(set(n["keys"])), "duplicate keys in node"
            assert n["keys"] == sorted(n["keys"]), "keys not sorted"
            assert len(n["keys"]) <= MAX_KEYS
            if n["leaf"]:
                assert n["children"] == []
                depth_of_leaf[nid] = depth
            else:
                assert len(n["children"]) == len(n["keys"]) + 1
                # Internal nodes are never merged: one-child shells are legal.
                assert len(n["children"]) >= 1
                for c in n["children"]:
                    assert str(c) in nodes
                nxt.extend(n["children"])
        level, depth = nxt, depth + 1
    assert set(map(int, nodes)) == reachable, "unreachable node in dump"
    assert len(set(depth_of_leaf.values())) == 1, "leaves at different depths"

    # Separator ordering: every key in child i lies in its separator slot.
    def check_ranges(nid, lo, hi):
        n = nodes[str(nid)]
        if n["leaf"]:
            for k in n["keys"]:
                assert lo <= k < hi, (k, lo, hi)
            return
        keys = n["keys"]
        bounds = [lo] + keys + [hi]
        for c, a, b in zip(n["children"], bounds[:-1], bounds[1:]):
            check_ranges(c, a, b)

    check_ranges(root, float("-inf"), float("inf"))

    # Leaf chain: next links follow the key order and cover all leaves.
    chain, nid = [], root
    while not nodes[str(nid)]["leaf"]:
        nid = nodes[str(nid)]["children"][0]
    leaf_ids = set(depth_of_leaf)
    seen = set()
    while nid is not None:
        assert nid in leaf_ids
        assert nid not in seen, "leaf chain repeats a leaf"
        seen.add(nid)
        chain.append(nodes[str(nid)]["keys"])
        nid = nodes[str(nid)]["next"]
    assert seen == leaf_ids, "leaf chain does not cover every leaf"
    flat = [k for keys in chain for k in keys]
    assert flat == sorted(flat), "leaf chain not globally sorted"
    assert len(flat) == len(set(flat)), "key duplicated across leaves"
    if expected is not None:
        assert flat == sorted(expected), (flat, sorted(expected))
    return flat


def check_scans(trace):
    """Every scan must equal the immutable snapshot of the exact version it
    pinned, intersected with its [lo, hi] window; keys sorted and distinct."""
    snaps = dict((v, frozenset(keys)) for v, keys in trace.export()["snapshots"])
    records = [r for r in trace.scans if r["version"] is not None]
    for rec in records:
        keys = rec["keys"]
        assert keys == sorted(keys), "scan keys not sorted"
        assert len(keys) == len(set(keys)), "scan returned duplicates"
        assert all(rec["lo"] <= k <= rec["hi"] for k in keys), "key outside range"
        v = rec["version"]
        assert v in snaps, ("scan pinned an unknown version", v)
        wanted = frozenset(keys)
        instant = {k for k in snaps[v] if rec["lo"] <= k <= rec["hi"]}
        assert instant == wanted, (rec, "does not equal version", v,
                                   "missing", sorted(instant - wanted),
                                   "extra", sorted(wanted - instant))


def run_many(workload, seeds):
    for seed in seeds:
        outcome, trace = run_traced(workload, seed)
        assert not outcome["deadlock"], (seed, outcome.get("waiting"))
        check_invariants(outcome)
        check_scans(trace)


class ProtocolTests(unittest.TestCase):
    def test_root_split(self):
        # Five inserts force the single root leaf to split and create a
        # new root, with scans interleaved on every seed.
        wl = {"initial": [],
              "threads": [
                  [{"op": "insert", "key": k} for k in (3, 7, 1, 9, 5)],
                  [{"op": "scan", "lo": 0, "hi": 100}],
                  [{"op": "scan", "lo": 2, "hi": 8}]]}
        run_many(wl, range(12))

    def test_cascading_splits(self):
        # Repeated inserts overflow leaves and then internal nodes, forcing
        # several splits in one operation's propagation loop.
        wl = {"initial": [],
              "threads": [
                  [{"op": "insert", "key": k} for k in range(1, 40, 2)],
                  [{"op": "insert", "key": k} for k in range(2, 40, 2)],
                  [{"op": "scan", "lo": 0, "hi": 100}],
                  [{"op": "scan", "lo": 10, "hi": 30}]]}
        run_many(wl, range(8))
        outcome, _ = run_traced(wl, 0)
        self.assertEqual(check_invariants(outcome), list(range(1, 40)))

    def test_leaf_merges(self):
        initial = list(range(1, 61))
        evens = [{"op": "delete", "key": k} for k in range(2, 61, 2)]
        odds = [{"op": "delete", "key": k} for k in range(59, 0, -2)]
        wl = {"initial": initial,
              "threads": [evens, odds,
                          [{"op": "scan", "lo": 0, "hi": 100}],
                          [{"op": "scan", "lo": 20, "hi": 45}]]}
        run_many(wl, range(8))
        outcome, _ = run_traced(wl, 0)
        self.assertEqual(check_invariants(outcome), [])

    def test_rightmost_leaf_merge(self):
        # Drain keys from the right end so the rightmost leaf goes underfull
        # and has to dance leftward with its left sibling.
        wl = {"initial": list(range(1, 25)),
              "threads": [
                  [{"op": "delete", "key": k} for k in range(24, 0, -1)],
                  [{"op": "scan", "lo": 0, "hi": 100}]]}
        run_many(wl, range(10))

    def test_mixed_workload(self):
        for name in ("mixed.json", "merge.json"):
            path = os.path.join(ROOT, "examples", name)
            import json
            with open(path, encoding="utf-8") as fh:
                wl = json.load(fh)
            run_many(wl, range(6))

    def test_random_fuzz(self):
        rng = random.Random(1234)
        universe = list(range(200))
        for trial in range(12):
            present = set(rng.sample(universe, rng.randint(5, 60)))
            scripts = [[], [], [], []]
            for _ in range(rng.randint(10, 30)):
                tid = rng.randrange(4)
                roll = rng.random()
                if roll < 0.4:
                    k = rng.choice(universe)
                    scripts[tid].append({"op": "insert", "key": k})
                elif roll < 0.75:
                    k = rng.choice(universe)
                    scripts[tid].append({"op": "delete", "key": k})
                else:
                    a, b = sorted(rng.sample(universe, 2))
                    scripts[tid].append({"op": "scan", "lo": a, "hi": b})
            wl = {"initial": sorted(present), "threads": scripts}
            run_many(wl, [trial, trial + 100, trial + 200])

    def test_no_deadlock_many_seeds(self):
        # A dense, adversarial mix designed to keep every latch busy.
        wl = {"initial": list(range(1, 33)),
              "threads": [
                  [{"op": "insert", "key": k} for k in range(33, 65)],
                  [{"op": "delete", "key": k} for k in range(1, 33)],
                  [{"op": "scan", "lo": 0, "hi": 100},
                   {"op": "scan", "lo": 5, "hi": 60}],
                  [{"op": "insert", "key": k} for k in range(65, 97)],
                  [{"op": "delete", "key": k} for k in range(32, 0, -2)]]}
        run_many(wl, range(20))


if __name__ == "__main__":
    unittest.main()
