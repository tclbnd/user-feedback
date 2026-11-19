import unittest

from runsec import run

DB = {"roles": {"u": {}}, "tables": {"t": {"owner": "o", "rls": False, "rows": [{"id": 1, "k": "a"}, {"id": 2, "k": "b"}]}},
      "policies": []}


class BasicTests(unittest.TestCase):
    def test_without_rls_everything_visible(self):
        out = run(DB, [{"as": {"role": "u"}, "select": "t"}, {"as": {"role": "u"}, "select": "t", "where": {"cmp": ["k", "=", "b"]}}])
        self.assertEqual([o["ids"] for o in out], [[1, 2], [2]])


if __name__ == "__main__":
    unittest.main()
