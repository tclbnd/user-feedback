import unittest

from pidx.planner import PartialIndex, execute

ROWS = [{"id": i, "status": ["active", "closed"][i % 2], "amount": i * 10} for i in range(1, 21)]
IX = [PartialIndex("active_only", "amount", {"cmp": ["status", "=", "active"]}, ROWS)]


class PlannerTests(unittest.TestCase):
    def test_exact_predicate_uses_index(self):
        out = execute({"cmp": ["status", "=", "active"]}, ROWS, IX)
        self.assertEqual(out["plan"], "active_only")
        self.assertEqual(out["ids"], [i for i in range(1, 21) if i % 2 == 0])

    def test_extra_conjunct_uses_index(self):
        out = execute({"and": [{"cmp": ["status", "=", "active"]}, {"cmp": ["amount", "<", 60]}]}, ROWS, IX)
        self.assertEqual((out["plan"], out["ids"]), ("active_only", [2, 4]))

    def test_unrelated_query_scans(self):
        self.assertEqual(execute({"cmp": ["amount", ">", 150]}, ROWS, IX)["plan"], "seq scan")


if __name__ == "__main__":
    unittest.main()
