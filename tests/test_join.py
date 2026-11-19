import unittest

from mjoin.planner import join

L = [{"id": f"l{i}", "k": i} for i in range(1, 21)]
R = [{"id": f"r{i}", "k": i * 2} for i in range(1, 15)]


class JoinTests(unittest.TestCase):
    def test_inner_unique_keys(self):
        out = join(L, R, "k", "k")
        self.assertEqual(out["method"], "merge join")
        self.assertEqual(sorted(out["rows"]), sorted((f"l{i}", f"r{i // 2}") for i in range(2, 21, 2)))

    def test_left_join_keeps_unmatched(self):
        out = join(L, R, "k", "k", "left")
        self.assertEqual(len(out["rows"]), 20)

    def test_tiny_inputs_use_nested_loop(self):
        out = join(L[:3], R[:3], "k", "k")
        self.assertEqual(out["method"], "nested loop")


if __name__ == "__main__":
    unittest.main()
