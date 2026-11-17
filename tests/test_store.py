import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pager  # noqa: E402
from store import Engine  # noqa: E402


class StoreTests(unittest.TestCase):
    def test_page_roundtrip(self):
        page = {"id": 3, "lsn": 42, "slots": ["a", "", "ccc", "d" * 58]}
        self.assertEqual(pager.decode(pager.encode(page), 3), page)

    def test_commit_then_flush(self):
        with tempfile.TemporaryDirectory() as d:
            e = Engine(d)
            for step in ({"op": "begin", "tx": 1},
                         {"op": "set", "tx": 1, "page": 1, "slot": 2, "value": "y"},
                         {"op": "commit", "tx": 1}, {"op": "flush", "page": 1}):
                e.apply(step)
            self.assertEqual(pager.PageFile(os.path.join(d, "pages.db")).read(1)["slots"][2], "y")
            types = [r["type"] for r in e.log.records()]
            self.assertEqual(types, ["BEGIN", "FPI", "UPDATE", "COMMIT", "END"])


if __name__ == "__main__":
    unittest.main()
