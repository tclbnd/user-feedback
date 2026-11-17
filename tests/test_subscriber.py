import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from subscriber import sync  # noqa: E402

LOG = [
    {"lsn": 1, "type": "begin", "xid": 1},
    {"lsn": 2, "type": "ddl", "xid": 1, "op": "create_table", "table": "t", "primary_key": "id",
     "columns": {"id": None, "v": None}},
    {"lsn": 3, "type": "insert", "xid": 1, "table": "t", "row": {"id": 1, "v": "a"}},
    {"lsn": 4, "type": "commit", "xid": 1},
    {"lsn": 5, "type": "begin", "xid": 2},
    {"lsn": 6, "type": "update", "xid": 2, "table": "t", "key": 1, "row": {"id": 1, "v": "b"}},
    {"lsn": 7, "type": "insert", "xid": 2, "table": "t", "row": {"id": 2, "v": "c"}},
    {"lsn": 8, "type": "commit", "xid": 2},
]


class SubscriberTests(unittest.TestCase):
    def test_full_sync(self):
        with tempfile.TemporaryDirectory() as d:
            pub, rep = os.path.join(d, "pub"), os.path.join(d, "rep")
            os.makedirs(pub)
            with open(os.path.join(pub, "changes.jsonl"), "w") as fh:
                fh.writelines(json.dumps(m) + "\n" for m in LOG)
            self.assertEqual(sync(rep, pub)["applied"], 2)
            with open(os.path.join(rep, "replica.json")) as fh:
                rows = json.load(fh)["tables"]["t"]["rows"]
            self.assertEqual(rows, [{"id": 1, "v": "b"}, {"id": 2, "v": "c"}])
            with open(os.path.join(rep, "position.json")) as fh:
                self.assertEqual(json.load(fh), {"commit_lsn": 8})

    def test_second_sync_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as d:
            pub, rep = os.path.join(d, "pub"), os.path.join(d, "rep")
            os.makedirs(pub)
            with open(os.path.join(pub, "changes.jsonl"), "w") as fh:
                fh.writelines(json.dumps(m) + "\n" for m in LOG)
            sync(rep, pub)
            self.assertEqual(sync(rep, pub)["applied"], 0)


if __name__ == "__main__":
    unittest.main()
