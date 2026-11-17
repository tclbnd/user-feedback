import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dialects  # noqa: E402
from sqlparse import Parser, render  # noqa: E402


class MigrateTests(unittest.TestCase):
    def test_parse_and_render(self):
        q = Parser("SELECT id, LOWER(name) AS n FROM t WHERE id = 3 ORDER BY id DESC LIMIT 5 OFFSET 10").parse()
        self.assertEqual((q["table"], q["limit"], q["offset"]), ("t", 5, 10))
        self.assertEqual(render(q), "SELECT id, LOWER(name) AS n FROM t WHERE id = 3 ORDER BY id DESC LIMIT 5 OFFSET 10")

    def test_target_timezone(self):
        rows = [{"id": 1, "ts": "2026-03-01T16:30:00Z"}]
        out = dialects.execute("target", "SELECT DATE_TRUNC('day', ts, '+08:00') AS d, TIMEZONE('+08:00', ts) AS t FROM x", rows)
        self.assertEqual(out, [{"d": "2026-03-02", "t": "2026-03-02 00:30:00"}])

    def test_legacy_case_insensitive(self):
        rows = [{"id": 1, "c": "bob"}, {"id": 2, "c": "Alice"}, {"id": 3, "c": "BOB"}]
        out = dialects.execute("legacy", "SELECT id FROM x WHERE c = 'Bob' ORDER BY c", rows)
        self.assertEqual([r["id"] for r in out], [1, 3])


if __name__ == "__main__":
    unittest.main()
