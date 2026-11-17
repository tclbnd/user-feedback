import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cluster import Cluster  # noqa: E402
from simulate import run  # noqa: E402


class SimulateTests(unittest.TestCase):
    def test_replica_replay_lag(self):
        c = Cluster([{"name": "r1", "lag": 10}])
        c.commit(0, [("a", 1)])
        self.assertEqual(c.read("r1", "a", 9)["version"], 0)
        self.assertEqual(c.read("r1", "a", 10), {"value": 1, "version": 1, "applied": 1})

    def test_reads_rotate_over_replicas(self):
        out = run({"replicas": [{"name": "r1", "lag": 1}, {"name": "r2", "lag": 1}],
                   "events": [{"t": 0, "op": "write", "session": "w", "key": "a", "value": 1}] +
                             [{"t": 10 + n, "op": "read", "session": "s", "key": "a"} for n in range(3)]})
        self.assertEqual([r["target"] for r in out["reads"]], ["r1", "r2", "r1"])


if __name__ == "__main__":
    unittest.main()
