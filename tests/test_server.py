import unittest

from rls.catalog import Catalog
from rls.server import serve

TABLES = {"orders": [{"id": 1, "tenant": "a", "status": "open"}, {"id": 2, "tenant": "b", "status": "open"}]}


class ServerTests(unittest.TestCase):
    def test_sessions_are_independent(self):
        cat = Catalog(TABLES)
        res = serve(cat, [{"tenant": "a", "plan": {"scan": "orders"}}, {"tenant": "b", "plan": {"scan": "orders"}}])
        self.assertEqual([r["ids"] for r in res], [[1], [2]])
        self.assertEqual(res[0]["rows_scanned"], 2)


if __name__ == "__main__":
    unittest.main()
