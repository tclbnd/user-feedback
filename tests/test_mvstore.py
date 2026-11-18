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

    def test_interleaved_sessions_acceptance(self):
        # Long transaction L, mid-abort W2, repeated updates of k by W1,
        # a delete, and xid wraparound; checks every snapshot read and the
        # version chain before/after each vacuum.
        script = [
            {"op": "begin", "s": "L"},                        # xid 3, long txn
            {"op": "begin", "s": "W1"},                       # xid 4
            {"op": "write", "s": "W1", "key": "k", "value": "a"},
            {"op": "write", "s": "W1", "key": "k", "value": "b"},   # same txn updates again
            {"op": "read", "s": "W1", "key": "k"},            # reads its own newest write
            {"op": "commit", "s": "W1"},
            {"op": "read", "s": "L", "key": "k"},             # L's snapshot predates W1
            {"op": "begin", "s": "W2"},                       # xid 5
            {"op": "write", "s": "W2", "key": "k", "value": "c"},
            {"op": "abort", "s": "W2"},                       # aborts mid-flight
            {"op": "read", "s": "L", "key": "k"},
            {"op": "begin", "s": "R"},                        # xid 6
            {"op": "read", "s": "R", "key": "k"},             # aborted "c" invisible
            {"op": "begin", "s": "W3"},                       # xid 7
            {"op": "write", "s": "W3", "key": "k", "value": "d"},
            {"op": "commit", "s": "W3"},
            {"op": "read", "s": "R", "key": "k"},             # repeatable read: still "b"
            {"op": "read", "s": "L", "key": "k"},
            {"op": "chain", "key": "k"},
            {"op": "vacuum"},                                 # L,R active: only aborted garbage goes
            {"op": "chain", "key": "k"},
            {"op": "commit", "s": "L"},
            {"op": "commit", "s": "R"},
            {"op": "vacuum"},                                 # now old committed versions go
            {"op": "chain", "key": "k"},
            {"op": "begin", "s": "D1"},                       # xid 8
            {"op": "write", "s": "D1", "key": "k", "value": None},  # delete
            {"op": "commit", "s": "D1"},
            {"op": "begin", "s": "R2"},                       # xid 9
            {"op": "read", "s": "R2", "key": "k"},
            {"op": "commit", "s": "R2"},
            {"op": "vacuum"},
            {"op": "chain", "key": "k"},
            {"op": "next_xid", "value": 4294967295},          # wraparound territory
            {"op": "begin", "s": "SL"},                       # xid 4294967295, long txn
            {"op": "read", "s": "SL", "key": "seq"},
            {"op": "begin", "s": "T1"},                       # xid 3 after wrap
            {"op": "write", "s": "T1", "key": "seq", "value": "w1"},
            {"op": "commit", "s": "T1"},
            {"op": "read", "s": "SL", "key": "seq"},          # post-snapshot write hidden
            {"op": "begin", "s": "T2"},                       # xid 4
            {"op": "write", "s": "T2", "key": "seq", "value": "w2"},
            {"op": "commit", "s": "T2"},
            {"op": "vacuum"},                                 # SL still active: nothing removable
            {"op": "chain", "key": "seq"},
            {"op": "commit", "s": "SL"},
            {"op": "vacuum"},
            {"op": "chain", "key": "seq"},
            {"op": "begin", "s": "T3"},                       # xid 5
            {"op": "read", "s": "T3", "key": "seq"},
            {"op": "commit", "s": "T3"},
        ]
        out = run(script)
        reads = [o["value"] for o in out if o["op"] == "read"]
        self.assertEqual(reads, ["b", None, None, "b", "b", None, None, None, None, "w2"])
        vacuums = [o["removed"] for o in out if o["op"] == "vacuum"]
        self.assertEqual(vacuums, [1, 2, 1, 0, 1])
        chains = [o["versions"] for o in out if o["op"] == "chain"]
        self.assertEqual(chains, [
            [[7, 0, "d"], [5, 0, "c"], [4, 7, "b"], [4, 4, "a"]],   # before 1st vacuum
            [[7, 0, "d"], [4, 7, "b"], [4, 4, "a"]],                # aborted head removed
            [[7, 0, "d"]],                                          # long txn done: fully reclaimed
            [[8, 0, None]],                                         # delete marker is the live head
            [[4, 0, "w2"], [3, 4, "w1"]],                           # SL pins the horizon across wrap
            [[4, 0, "w2"]],
        ])
        xids = [o["xid"] for o in out if o["op"] == "begin"]
        self.assertEqual(xids, [3, 4, 5, 6, 7, 8, 9, 4294967295, 3, 4, 5])


if __name__ == "__main__":
    unittest.main()
