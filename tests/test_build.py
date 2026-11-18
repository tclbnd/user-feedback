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


if __name__ == "__main__":
    unittest.main()
