import unittest

from pidx.planner import PartialIndex
from pidx.prepared import PreparedQuery, bind

ROWS = [{"id": i, "status": ["active", "closed"][i % 2], "amount": i * 10} for i in range(1, 21)]
IX = [PartialIndex("active_only", "amount", {"cmp": ["status", "=", "active"]}, ROWS)]


class PreparedTests(unittest.TestCase):
    def test_bind(self):
        self.assertEqual(bind({"cmp": ["amount", ">", {"param": "x"}]}, {"x": 5}), {"cmp": ["amount", ">", 5]})

    def test_plan_reused(self):
        q = PreparedQuery({"and": [{"cmp": ["status", "=", "active"]}, {"cmp": ["amount", ">", {"param": "x"}]}]}, IX)
        a = q.execute({"x": 100}, ROWS)
        b = q.execute({"x": 150}, ROWS)
        self.assertEqual((a["plan"], b["plan"], q.plannings), ("active_only", "active_only", 1))
        self.assertEqual(b["ids"], [16, 18, 20])


if __name__ == "__main__":
    unittest.main()
