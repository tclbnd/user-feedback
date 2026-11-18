import unittest

from runbuild import run


class BuildTests(unittest.TestCase):
    def test_online_build_without_writes(self):
        out = run({"rows": 120, "mode": "online"})
        self.assertEqual(out["state"], "valid")
        self.assertEqual(out["mismatches"], [])

    def test_offline_build_is_consistent(self):
        out = run({"rows": 120, "mode": "offline",
                   "writes": [{"tick": 1, "op": "insert", "key": 999, "val": "x"}]})
        self.assertEqual(out["mismatches"], [])

    def test_writes_after_build_are_indexed(self):
        out = run({"rows": 40, "writes": [{"tick": 10, "op": "insert", "key": 999, "val": "x"},
                                          {"tick": 11, "op": "update", "key": 5, "val": "y"}]})
        self.assertEqual(out["mismatches"], [])

    def test_online_build_indexes_writes_that_commit_mid_build(self):
        # Latency pushes these commits well into the build window; the first
        # ones were planned before the index existed and must still show up.
        writes = [{"tick": 1, "op": "insert", "key": 9001, "val": "n1", "latency": 5},
                  {"tick": 2, "op": "insert", "key": 9002, "val": "n2", "latency": 4},
                  {"tick": 3, "op": "update", "key": 7, "val": "u7", "latency": 6},
                  {"tick": 4, "op": "update", "key": 8, "new_key": 9008, "val": "m8", "latency": 3},
                  {"tick": 5, "op": "delete", "key": 9, "latency": 4},
                  {"tick": 20, "op": "insert", "key": 9003, "val": "n3", "latency": 0}]
        out = run({"rows": 120, "mode": "online", "writes": writes})
        self.assertEqual(out["state"], "valid")
        self.assertEqual(out["mismatches"], [])

    def test_online_build_does_not_block_writers(self):
        writes = [{"tick": t, "op": "insert", "key": 9000 + t, "val": "n%d" % t}
                  for t in range(1, 40)]
        out = run({"rows": 400, "mode": "online", "writes": writes})
        self.assertEqual(out["state"], "valid")
        self.assertEqual(out["mismatches"], [])
        self.assertEqual(out["blocked_ticks"], 0)

    def test_online_build_leaves_no_stale_entries(self):
        writes = [{"tick": 2, "op": "update", "key": 11, "new_key": 7011, "val": "m11", "latency": 2},
                  {"tick": 4, "op": "update", "key": 12, "val": "u12", "latency": 2},
                  {"tick": 6, "op": "delete", "key": 13, "latency": 2}]
        out = run({"rows": 200, "mode": "online", "writes": writes})
        self.assertEqual(out["mismatches"], [])
        self.assertEqual(out["bloat"]["entries"], out["bloat"]["live"])

    def test_writes_maintain_index_after_build(self):
        writes = [{"tick": 60, "op": "update", "key": 3, "new_key": 8003, "val": "m3"},
                  {"tick": 61, "op": "delete", "key": 4},
                  {"tick": 62, "op": "insert", "key": 8004, "val": "n4"}]
        out = run({"rows": 120, "mode": "online", "writes": writes})
        self.assertEqual(out["mismatches"], [])
        self.assertEqual(out["bloat"]["entries"], out["bloat"]["live"])


if __name__ == "__main__":
    unittest.main()
