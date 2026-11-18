import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from node import Node  # noqa: E402


class HaTests(unittest.TestCase):
    def test_lagging_replica_catches_up(self):
        leader, replica = Node("n1"), Node("n2")
        leader.log = [{"term": 1, "key": "a", "value": "1"}, {"term": 1, "key": "b", "value": "2"}]
        replica.log = leader.log[:1]
        self.assertTrue(replica.receive(leader))
        self.assertEqual(replica.kv(), {"a": "1", "b": "2"})
        self.assertEqual(replica.conflicts, [])

    def test_steady_cluster(self):
        spec = {"nodes": ["n1", "n2", "n3"], "lease": 5, "ticks": 6,
                "writes": [{"t": t, "node": "n1", "key": "k", "value": str(t)} for t in range(1, 5)]}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.json")
            with open(path, "w") as fh:
                json.dump(spec, fh)
            subprocess.run([sys.executable, os.path.join(ROOT, "ha.py"), "run", path, os.path.join(d, "out")],
                           check=True, capture_output=True)
            with open(os.path.join(d, "out", "state.json")) as fh:
                state = json.load(fh)
        self.assertEqual({n: s["kv"] for n, s in state.items()}, {n: {"k": "4"} for n in ("n1", "n2", "n3")})


if __name__ == "__main__":
    unittest.main()
