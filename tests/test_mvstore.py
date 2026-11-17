import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mvstore import run  # noqa: E402
from txn import Snapshot, TxnManager, precedes  # noqa: E402


def reads(out):
    return [o["value"] for o in out if o["op"] == "read"]


def chains(out):
    return [o["versions"] for o in out if o["op"] == "chain"]


def vacuums(out):
    return [o["removed"] for o in out if o["op"] == "vacuum"]


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
        self.assertEqual(reads(out), [None, "1"])


class CircularOrderTests(unittest.TestCase):
    def test_precedes_is_circular(self):
        self.assertTrue(precedes(3, 5))
        self.assertFalse(precedes(5, 3))
        self.assertFalse(precedes(5, 5))
        # across the wraparound boundary: 4294967295 is immediately older than 3
        self.assertTrue(precedes(4294967295, 3))
        self.assertFalse(precedes(3, 4294967295))
        self.assertTrue(precedes(4294967294, 4))
        self.assertFalse(precedes(4, 4294967294))

    def test_snapshot_in_progress_wraparound(self):
        snap = Snapshot(3, set())  # next xid is 3, nothing running
        self.assertTrue(snap.in_progress(3))
        self.assertTrue(snap.in_progress(4))
        self.assertFalse(snap.in_progress(4294967294))
        snap2 = Snapshot(5, {3})
        self.assertTrue(snap2.in_progress(3))       # listed in xip
        self.assertTrue(snap2.in_progress(6))       # not before xmax
        self.assertFalse(snap2.in_progress(4))

    def test_horizon_includes_snapshot_xmin_after_older_txn_finishes(self):
        tm = TxnManager()
        xid_a, _ = tm.begin()            # 3
        xid_l, snap_l = tm.begin()       # 4, snapshot xip={3} -> xmin 3
        tm.finish(xid_a, "committed")    # 3 gone, but L's snapshot still needs it
        self.assertEqual(snap_l.xmin, 3)
        self.assertEqual(tm.horizon(), 3)  # not the youngest active xid 4


class LongTransactionTests(unittest.TestCase):
    def test_vacuum_cannot_remove_versions_a_long_snapshot_still_reads(self):
        # A and C start before the long reader L; A commits after L's snapshot,
        # C replaces A's row afterwards. A naive horizon (youngest active xid)
        # would delete A's version while L still needs it -> L would read null.
        # A commits before anyone else begins; C starts next (older xid than L),
        # then L snapshots. C's later replacement lands while L's snapshot still
        # needs A's version. A naive horizon (youngest active xid = C) would
        # reclaim A's version and L's repeatable read would turn into null.
        out = run([
            {"op": "begin", "s": "A"}, {"op": "write", "s": "A", "key": "k", "value": "v1"},
            {"op": "commit", "s": "A"},
            {"op": "begin", "s": "C"},
            {"op": "begin", "s": "L"},
            {"op": "read", "s": "L", "key": "k"},
            {"op": "write", "s": "C", "key": "k", "value": "v2"}, {"op": "commit", "s": "C"},
            {"op": "chain", "key": "k"},
            {"op": "vacuum"},
            {"op": "read", "s": "L", "key": "k"},
            {"op": "chain", "key": "k"},
            {"op": "commit", "s": "L"},
            {"op": "vacuum"},
            {"op": "chain", "key": "k"},
            {"op": "begin", "s": "D"}, {"op": "read", "s": "D", "key": "k"},
            {"op": "commit", "s": "D"},
        ])
        self.assertEqual(reads(out)[0], "v1")
        self.assertEqual(chains(out)[0], [[4, 0, "v2"], [3, 4, "v1"]])
        self.assertEqual(vacuums(out)[0], 0)            # nothing reclaimable while L runs
        self.assertEqual(reads(out)[1], "v1")           # long txn must NOT see null
        self.assertEqual(len(chains(out)[1]), 2)        # chain untouched by the vacuum
        self.assertEqual(vacuums(out)[1], 1)            # old version reclaimed once L ends
        self.assertEqual(chains(out)[2], [[4, 0, "v2"]])
        self.assertEqual(reads(out)[1], "v1")           # L's own repeatable read held
        self.assertEqual(reads(out)[2], "v2")           # fresh transaction sees the head


class AbortTests(unittest.TestCase):
    def test_aborted_writer_never_hides_the_prior_version_and_is_reclaimed(self):
        out = run([
            {"op": "begin", "s": "load"}, {"op": "write", "s": "load", "key": "stock", "value": "12"},
            {"op": "commit", "s": "load"},
            {"op": "begin", "s": "r"}, {"op": "read", "s": "r", "key": "stock"},
            {"op": "begin", "s": "x"}, {"op": "write", "s": "x", "key": "stock", "value": "0"},
            {"op": "abort", "s": "x"},
            {"op": "read", "s": "r", "key": "stock"},
            {"op": "begin", "s": "y"}, {"op": "write", "s": "y", "key": "stock", "value": "-1"},
            {"op": "abort", "s": "y"},
            {"op": "read", "s": "r", "key": "stock"},
            {"op": "chain", "key": "stock"},
            {"op": "vacuum"},
            {"op": "chain", "key": "stock"},
            {"op": "read", "s": "r", "key": "stock"},
            {"op": "commit", "s": "r"},
            {"op": "vacuum"},
            {"op": "chain", "key": "stock"},
            {"op": "begin", "s": "z"}, {"op": "read", "s": "z", "key": "stock"},
            {"op": "commit", "s": "z"},
        ])
        self.assertEqual(reads(out), ["12", "12", "12", "12", "12"])
        # both aborted versions sit on the chain; y's write re-marks the live one
        self.assertEqual(chains(out)[0],
                         [[6, 0, "-1"], [5, 0, "0"], [3, 6, "12"]])
        self.assertEqual(vacuums(out)[0], 1)            # only the inner aborted version goes
        self.assertEqual(chains(out)[1], [[6, 0, "-1"], [3, 6, "12"]])  # head always kept
        self.assertEqual(vacuums(out)[1], 0)            # no empty-run growth / repeated churn
        self.assertEqual(len(chains(out)[2]), 2)

    def test_chain_head_survives_when_every_version_is_aborted(self):
        out = run([
            {"op": "begin", "s": "A"}, {"op": "write", "s": "A", "key": "k", "value": "x"},
            {"op": "abort", "s": "A"},
            {"op": "vacuum"}, {"op": "chain", "key": "k"},
            {"op": "vacuum"},
            {"op": "begin", "s": "B"}, {"op": "read", "s": "B", "key": "k"},
            {"op": "commit", "s": "B"},
        ])
        self.assertEqual(vacuums(out), [0, 0])
        self.assertEqual(chains(out)[0], [[3, 0, "x"]])  # head retained even if aborted
        self.assertEqual(reads(out), [None])


class RepeatedUpdateTests(unittest.TestCase):
    def test_several_updates_inside_one_transaction(self):
        out = run([
            {"op": "begin", "s": "A"},
            {"op": "write", "s": "A", "key": "k", "value": "a"},
            {"op": "write", "s": "A", "key": "k", "value": "b"},
            {"op": "write", "s": "A", "key": "k", "value": "c"},
            {"op": "read", "s": "A", "key": "k"},
            {"op": "chain", "key": "k"},
            {"op": "commit", "s": "A"},
            {"op": "begin", "s": "B"}, {"op": "read", "s": "B", "key": "k"},
            {"op": "vacuum"},
            {"op": "chain", "key": "k"},
            {"op": "read", "s": "B", "key": "k"},
        ])
        self.assertEqual(reads(out)[0], "c")           # writer sees its own latest version
        self.assertEqual(chains(out)[0],
                         [[3, 0, "c"], [3, 3, "b"], [3, 3, "a"]])
        self.assertEqual(reads(out)[1], "c")
        self.assertEqual(vacuums(out), [2])            # two superseded own versions removed
        self.assertEqual(chains(out)[1], [[3, 0, "c"]])  # newest version kept
        self.assertEqual(reads(out)[2], "c")

    def test_row_updated_by_a_series_of_committing_transactions_under_a_long_reader(self):
        out = run([
            {"op": "begin", "s": "A"}, {"op": "write", "s": "A", "key": "k", "value": "v0"},
            {"op": "commit", "s": "A"},
            {"op": "begin", "s": "L"}, {"op": "read", "s": "L", "key": "k"},
            {"op": "begin", "s": "B"}, {"op": "write", "s": "B", "key": "k", "value": "v1"},
            {"op": "commit", "s": "B"},
            {"op": "read", "s": "L", "key": "k"},
            {"op": "begin", "s": "C"}, {"op": "write", "s": "C", "key": "k", "value": "v2"},
            {"op": "commit", "s": "C"},
            {"op": "read", "s": "L", "key": "k"},
            {"op": "chain", "key": "k"},
            {"op": "vacuum"},
            {"op": "chain", "key": "k"},
            {"op": "commit", "s": "L"},
            {"op": "vacuum"},
            {"op": "chain", "key": "k"},
            {"op": "begin", "s": "D"}, {"op": "read", "s": "D", "key": "k"},
            {"op": "commit", "s": "D"},
        ])
        self.assertEqual(reads(out)[:3], ["v0", "v0", "v0"])  # repeatable read throughout
        self.assertEqual(chains(out)[0],
                         [[6, 0, "v2"], [5, 6, "v1"], [3, 5, "v0"]])
        self.assertEqual(vacuums(out)[0], 0)
        self.assertEqual(len(chains(out)[1]), 3)        # long reader freezes the whole chain
        self.assertEqual(vacuums(out)[1], 2)
        self.assertEqual(chains(out)[2], [[6, 0, "v2"]])
        self.assertEqual(reads(out)[3], "v2")


class WraparoundTests(unittest.TestCase):
    def test_committed_versions_visible_across_wraparound(self):
        out = run([
            {"op": "next_xid", "value": 4294967294},
            {"op": "begin", "s": "A"}, {"op": "write", "s": "A", "key": "seq", "value": "41"},
            {"op": "commit", "s": "A"},
            {"op": "begin", "s": "B"}, {"op": "write", "s": "B", "key": "seq", "value": "42"},
            {"op": "commit", "s": "B"},
            {"op": "begin", "s": "C"}, {"op": "read", "s": "C", "key": "seq"},
            {"op": "commit", "s": "C"},
            {"op": "vacuum"}, {"op": "chain", "key": "seq"},
        ])
        self.assertEqual(reads(out), ["42"])
        self.assertEqual(vacuums(out), [1])
        self.assertEqual(chains(out)[0], [[4294967295, 0, "42"]])

    def test_long_reader_spanning_the_xid_boundary(self):
        out = run([
            {"op": "next_xid", "value": 4294967294},
            {"op": "begin", "s": "A"}, {"op": "write", "s": "A", "key": "k", "value": "old"},
            {"op": "commit", "s": "A"},
            {"op": "begin", "s": "L"}, {"op": "read", "s": "L", "key": "k"},
            {"op": "begin", "s": "B"}, {"op": "write", "s": "B", "key": "k", "value": "new"},
            {"op": "commit", "s": "B"},
            {"op": "read", "s": "L", "key": "k"},
            {"op": "chain", "key": "k"},
            {"op": "vacuum"},
            {"op": "read", "s": "L", "key": "k"},
            {"op": "commit", "s": "L"},
            {"op": "vacuum"},
            {"op": "chain", "key": "k"},
            {"op": "begin", "s": "C"}, {"op": "read", "s": "C", "key": "k"},
            {"op": "commit", "s": "C"},
        ])
        self.assertEqual(reads(out), ["old", "old", "old", "new"])
        self.assertEqual(chains(out)[0], [[3, 0, "new"], [4294967294, 3, "old"]])
        self.assertEqual(vacuums(out)[0], 0)            # pre-boundary version still needed
        self.assertEqual(vacuums(out)[1], 1)
        self.assertEqual(chains(out)[1], [[3, 0, "new"]])


class VacuumEdgeTests(unittest.TestCase):
    def test_repeated_vacuum_is_stable_and_does_not_grow_chains(self):
        out = run([
            {"op": "begin", "s": "A"}, {"op": "write", "s": "A", "key": "k", "value": "1"},
            {"op": "commit", "s": "A"},
            {"op": "vacuum"}, {"op": "vacuum"}, {"op": "vacuum"},
            {"op": "chain", "key": "k"},
        ])
        self.assertEqual(vacuums(out), [0, 0, 0])
        self.assertEqual(chains(out)[0], [[3, 0, "1"]])

    def test_delete_marker_is_visible_as_null_and_old_version_is_reclaimed(self):
        out = run([
            {"op": "begin", "s": "A"}, {"op": "write", "s": "A", "key": "k", "value": "1"},
            {"op": "commit", "s": "A"},
            {"op": "begin", "s": "B"}, {"op": "write", "s": "B", "key": "k", "value": None},
            {"op": "commit", "s": "B"},
            {"op": "begin", "s": "C"}, {"op": "read", "s": "C", "key": "k"},
            {"op": "commit", "s": "C"},
            {"op": "chain", "key": "k"},
            {"op": "vacuum"},
            {"op": "chain", "key": "k"},
            {"op": "begin", "s": "D"}, {"op": "read", "s": "D", "key": "k"},
            {"op": "commit", "s": "D"},
        ])
        self.assertEqual(reads(out), [None, None])
        self.assertEqual(chains(out)[0], [[4, 0, None], [3, 4, "1"]])
        self.assertEqual(vacuums(out), [1])
        self.assertEqual(chains(out)[1], [[4, 0, None]])


if __name__ == "__main__":
    unittest.main()
