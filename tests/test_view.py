import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from view import View  # noqa: E402


def ins(table, **row):
    return {"op": "insert", "table": table, "row": row}


class ViewTests(unittest.TestCase):
    def test_inserts_aggregate_by_region(self):
        v = View()
        for s in (ins("customers", id=1, region="eu"), ins("orders", id=1, cust=1, amount=10, status="paid"),
                  ins("orders", id=2, cust=1, amount=30, status="paid")):
            v.apply(s)
        self.assertEqual(v.rows(), [{"region": "eu", "n": 2, "total": 40, "avg": 20.0, "lo": 10, "hi": 30}])

    def test_void_orders_are_filtered(self):
        v = View()
        v.apply(ins("customers", id=1, region="eu"))
        v.apply(ins("orders", id=1, cust=1, amount=10, status="void"))
        self.assertEqual(v.rows(), [])


if __name__ == "__main__":
    unittest.main()
