import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import heap  # noqa: E402
from check import check  # noqa: E402

INDEXES = [{"name": "email", "column": "email", "unique": True},
           {"name": "city", "column": "city", "unique": False}]


class IndexTests(unittest.TestCase):
    def test_write_path_keeps_indexes(self):
        with tempfile.TemporaryDirectory() as d:
            t = heap.create(d, ["id", "email", "city"], INDEXES, [[1, "a@x", "Oslo"], [2, "b@x", "Lima"]])
            t.update((0, 1), [2, "b@x", "Oslo"])
            t.delete((0, 0))
            t.save()
            t = heap.Table(d)
            self.assertEqual(t.entries["city"], [("Oslo", (0, 1))])
            self.assertEqual(check(t), [])

    def test_lost_entry_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            t = heap.create(d, ["id", "email", "city"], INDEXES, [[1, "a@x", "Oslo"]])
            t.drop_entry("email", "a@x", (0, 0))
            self.assertEqual(check(t), [{"index": "email", "kind": "missing", "key": "a@x", "rowid": [0, 0]}])


if __name__ == "__main__":
    unittest.main()
