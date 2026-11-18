import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mvstore import run  # noqa: E402


class MvstoreTests(unittest.TestCase):
    def test_committed_write_is_read_by_later_transaction(self):
        out = run([{"op": "begin", "s": "A"}, {"op": "write", "s": "A", "key": "k", "value": "1"},
                   {"op": "commit", "s": "A"}, {"op": "begin", "s": "B"},
                   {"op": "read", "s": "B", "key": "k"}])
        self.assertEqual(out[-1]["value"], "1")

    def test_uncommitted_write_is_hidden(self):
        out = run([{"op": "begin", "s": "A"}, {"op": "write", "s": "A", "key": "k", "value": "1"},
                   {"op": "begin", "s": "B"}, {"op": "read", "s": "B", "key": "k"},
                   {"op": "read", "s": "A", "key": "k"}])
        self.assertEqual([o["value"] for o in out if o["op"] == "read"], [None, "1"])


if __name__ == "__main__":
    unittest.main()
