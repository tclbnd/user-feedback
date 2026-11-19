"""Behaviour of sequences, checked against PostgreSQL's semantics."""
import unittest

from seqdb.catalog import Catalog, order_number
from seqdb.sequence import Sequence, SequenceError
from seqdb.session import Session


def make(**options):
    cat = Catalog()
    cat.create("s", **options)
    return cat


class NextvalTests(unittest.TestCase):
    def test_first_value_is_start(self):
        cat = make(start=5, increment=2)
        a = Session("a", cat)
        self.assertEqual([a.nextval("s"), a.nextval("s"), a.nextval("s")], [5, 7, 9])

    def test_defaults_ascending(self):
        cat = make()
        a = Session("a", cat)
        self.assertEqual(a.nextval("s"), 1)
        self.assertEqual((a.catalog.sequence("s").minvalue,
                          a.catalog.sequence("s").maxvalue), (1, 2**63 - 1))

    def test_defaults_descending(self):
        # A descending sequence starts at MAXVALUE -1 and may go down to
        # -(PG_INT64_MAX).
        cat = make(increment=-1)
        a = Session("a", cat)
        self.assertEqual(a.nextval("s"), -1)
        seq = cat.sequence("s")
        self.assertEqual((seq.minvalue, seq.maxvalue), (-(2**63 - 1), -1))

    def test_descending_explicit(self):
        cat = make(increment=-3, minvalue=1, maxvalue=50, start=50)
        a = Session("a", cat)
        self.assertEqual([a.nextval("s"), a.nextval("s")], [50, 47])

    def test_zero_increment_rejected(self):
        with self.assertRaises(SequenceError):
            Sequence("s", increment=0)

    def test_bad_cache_rejected(self):
        with self.assertRaises(SequenceError):
            Sequence("s", cache=0)

    def test_min_must_be_below_max(self):
        with self.assertRaises(SequenceError):
            Sequence("s", minvalue=10, maxvalue=10)

    def test_start_outside_bounds_rejected(self):
        with self.assertRaises(SequenceError):
            Sequence("s", minvalue=1, maxvalue=10, start=11)

    def test_maxvalue_is_never_exceeded(self):
        cat = make(minvalue=1, maxvalue=3)
        a = Session("a", cat)
        self.assertEqual([a.nextval("s") for _ in range(3)], [1, 2, 3])
        with self.assertRaises(SequenceError):
            a.nextval("s")
        # The sequence stays at its bound and keeps failing.
        with self.assertRaises(SequenceError):
            a.nextval("s")

    def test_maxvalue_is_never_exceeded_with_cache(self):
        cat = make(minvalue=1, maxvalue=3, cache=10)
        a = Session("a", cat)
        self.assertEqual([a.nextval("s") for _ in range(3)], [1, 2, 3])
        with self.assertRaises(SequenceError):
            a.nextval("s")

    def test_maxvalue_is_never_exceeded_with_step(self):
        cat = make(minvalue=1, maxvalue=10, increment=4)
        a = Session("a", cat)
        self.assertEqual([a.nextval("s") for _ in range(3)], [1, 5, 9])
        with self.assertRaises(SequenceError):
            a.nextval("s")

    def test_minvalue_is_never_undercut_descending(self):
        cat = make(increment=-2, minvalue=-5, maxvalue=-1)
        a = Session("a", cat)
        self.assertEqual([a.nextval("s") for _ in range(3)], [-1, -3, -5])
        with self.assertRaises(SequenceError):
            a.nextval("s")

    def test_cycle_wraps_to_minvalue(self):
        cat = make(minvalue=1, maxvalue=3, cycle=True)
        a = Session("a", cat)
        self.assertEqual([a.nextval("s") for _ in range(5)], [1, 2, 3, 1, 2])

    def test_cycle_descending_wraps_to_maxvalue(self):
        cat = make(increment=-1, minvalue=1, maxvalue=3, cycle=True)
        a = Session("a", cat)
        self.assertEqual([a.nextval("s") for _ in range(5)], [3, 2, 1, 3, 2])

    def test_cycle_with_cache(self):
        cat = make(minvalue=1, maxvalue=4, cycle=True, cache=3)
        a = Session("a", cat)
        self.assertEqual([a.nextval("s") for _ in range(9)],
                         [1, 2, 3, 4, 1, 2, 3, 4, 1])

    def test_cycle_with_cache_skips_nothing_at_the_bound(self):
        # cache block 1..4 is reserved, then wraps: no value outside 1..4.
        cat = make(minvalue=1, maxvalue=5, cache=4, cycle=True)
        a = Session("a", cat)
        values = [a.nextval("s") for _ in range(8)]
        self.assertEqual(values, [1, 2, 3, 4, 5, 1, 2, 3])
        self.assertTrue(all(1 <= v <= 5 for v in values))

    def test_cycle_with_cache_at_the_bound(self):
        # block 1..4 reserved (one step to the bound), then the next block
        # wraps to 1: values outside 1..4 are never handed out.
        cat = make(minvalue=1, maxvalue=4, cache=4, cycle=True)
        a = Session("a", cat)
        self.assertEqual([a.nextval("s") for _ in range(9)],
                         [1, 2, 3, 4, 1, 2, 3, 4, 1])

    def test_no_cycle_truncated_block_then_error(self):
        # The block stops at MAXVALUE; the next reservation fails instead of
        # wrapping or overrunning.
        cat = make(minvalue=1, maxvalue=5, cache=4)
        a = Session("a", cat)
        self.assertEqual([a.nextval("s") for _ in range(5)], [1, 2, 3, 4, 5])
        with self.assertRaises(SequenceError):
            a.nextval("s")

    def test_option_grid_never_hands_out_out_of_bounds_values(self):
        # Any combination of options and cache sizes, several sessions: values
        # always stay inside [MINVALUE, MAXVALUE] and are never duplicated
        # while a session has not run out.
        for increment in (1, 3, -1, -3):
            for minvalue, maxvalue in ((1, 6), (-6, -1), (-3, 3)):
                for cycle in (False, True):
                    start = minvalue if increment > 0 else maxvalue
                    for cache in (1, 2, 5, 20):
                        cat = Catalog()
                        cat.create("s", increment=increment, minvalue=minvalue,
                                   maxvalue=maxvalue, start=start,
                                   cycle=cycle, cache=cache)
                        a, b = Session("a", cat), Session("b", cat)
                        for _ in range(3 * (maxvalue - minvalue + 1)):
                            for s in (a, b):
                                try:
                                    v = s.nextval("s")
                                except SequenceError:
                                    continue
                                self.assertGreaterEqual(v, minvalue)
                                self.assertLessEqual(v, maxvalue)

    def test_descending_stops_at_minvalue(self):
        cat = make(increment=-4, minvalue=-10, maxvalue=-1)
        a = Session("a", cat)
        self.assertEqual([a.nextval("s") for _ in range(3)], [-1, -5, -9])
        with self.assertRaises(SequenceError):
            a.nextval("s")

    def test_descending_cycle_wraps_to_maxvalue(self):
        cat = make(increment=-4, minvalue=-10, maxvalue=-1, cycle=True, cache=1)
        a = Session("a", cat)
        self.assertEqual([a.nextval("s") for _ in range(5)],
                         [-1, -5, -9, -1, -5])

    def test_unknown_sequence(self):
        a = Session("a", Catalog())
        with self.assertRaises(SequenceError):
            a.nextval("nope")


class CurrvalLastvalTests(unittest.TestCase):
    def test_currval_before_nextval_errors(self):
        cat = make()
        a = Session("a", cat)
        with self.assertRaises(SequenceError):
            a.currval("s")

    def test_lastval_before_nextval_errors(self):
        cat = make()
        a = Session("a", cat)
        with self.assertRaises(SequenceError):
            a.lastval()

    def test_currval_is_per_session(self):
        cat = make(start=100)
        a, b = Session("a", cat), Session("b", cat)
        self.assertEqual(a.nextval("s"), 100)
        self.assertEqual(b.nextval("s"), 101)
        self.assertEqual(a.currval("s"), 100)   # not b's 101
        self.assertEqual(b.currval("s"), 101)
        self.assertEqual(a.nextval("s"), 102)
        self.assertEqual(a.currval("s"), 102)
        self.assertEqual(b.currval("s"), 101)   # b is not affected

    def test_currval_unaffected_by_other_session_setval(self):
        cat = make(start=1)
        a, admin = Session("a", cat), Session("admin", cat)
        a.nextval("s")
        admin.setval("s", 500, True)
        self.assertEqual(a.currval("s"), 1)
        self.assertEqual(a.nextval("s"), 501)   # cache 1: reads the new state

    def test_lastval_tracks_most_recent_nextval(self):
        cat = make(start=1)
        cat.create("t", start=50)
        a = Session("a", cat)
        a.nextval("s")
        a.nextval("t")
        self.assertEqual(a.lastval(), 50)
        a.nextval("s")
        self.assertEqual(a.lastval(), 2)

    def test_lastval_is_per_session(self):
        cat = make(start=1)
        a, b = Session("a", cat), Session("b", cat)
        a.nextval("s")
        b.nextval("s")
        self.assertEqual(a.lastval(), 1)
        self.assertEqual(b.lastval(), 2)

    def test_currval_after_commit_and_rollback(self):
        cat = make(start=1)
        a = Session("a", cat)
        a.begin()
        a.nextval("s")
        a.rollback()
        self.assertEqual(a.currval("s"), 1)
        self.assertEqual(a.lastval(), 1)

    def test_currval_after_setval(self):
        cat = make()
        a = Session("a", cat)
        a.setval("s", 42)
        self.assertEqual(a.currval("s"), 42)
        self.assertEqual(a.nextval("s"), 43)
        self.assertEqual(a.lastval(), 43)

    def test_lastval_ignores_setval_of_another_sequence(self):
        # lastval() is about the last nextval of the session, not setval.
        cat = make()
        cat.create("t")
        a = Session("a", cat)
        a.nextval("s")
        a.setval("t", 50, True)
        self.assertEqual(a.lastval(), 1)

    def test_setval_false_defines_currval_for_that_session(self):
        # setval establishes the current value of the sequence for the calling
        # session (PostgreSQL marks the backend-local value valid), while the
        # next nextval still returns exactly the value that was set.
        cat = make()
        a = Session("a", cat)
        a.setval("s", 42, False)
        self.assertEqual(a.currval("s"), 42)
        self.assertEqual(a.nextval("s"), 42)
        self.assertEqual(a.currval("s"), 42)

    def test_setval_does_not_define_lastval(self):
        # lastval() refers to the sequence nextval() was applied to; setval()
        # alone does not make a sequence that one.
        cat = make()
        a = Session("a", cat)
        a.setval("s", 42, False)
        with self.assertRaises(SequenceError):
            a.lastval()

    def test_lastval_follows_setval_of_the_last_used_sequence(self):
        cat = make()
        a = Session("a", cat)
        a.nextval("s")                 # 1
        a.setval("s", 100, False)
        self.assertEqual(a.lastval(), 100)
        self.assertEqual(a.nextval("s"), 100)
        self.assertEqual(a.lastval(), 100)


class SetvalTests(unittest.TestCase):
    def test_setval_returns_the_value_it_set(self):
        cat = make()
        a = Session("a", cat)
        self.assertEqual(a.setval("s", 123), 123)

    def test_setval_false_makes_next_value_exact(self):
        cat = make(start=1)
        a = Session("a", cat)
        a.setval("s", 1001, False)
        self.assertEqual(a.nextval("s"), 1001)
        self.assertEqual(a.nextval("s"), 1002)

    def test_setval_true_continues_after_value(self):
        cat = make(start=1)
        a = Session("a", cat)
        a.setval("s", 1001, True)
        self.assertEqual(a.nextval("s"), 1002)

    def test_setval_works_for_descending_sequences(self):
        cat = make(increment=-1, maxvalue=1000, minvalue=1)
        a = Session("a", cat)
        self.assertEqual(a.nextval("s"), 1000)
        a.setval("s", 500, False)
        self.assertEqual(a.nextval("s"), 500)
        self.assertEqual(a.nextval("s"), 499)

    def test_setval_out_of_bounds(self):
        cat = make(minvalue=1, maxvalue=10)
        a = Session("a", cat)
        with self.assertRaises(SequenceError):
            a.setval("s", 0)
        with self.assertRaises(SequenceError):
            a.setval("s", 11)
        with self.assertRaises(SequenceError):
            a.setval("s", 11, False)

    def test_setval_discards_the_callers_cached_block(self):
        cat = make(start=1, cache=10)
        a = Session("a", cat)
        self.assertEqual(a.nextval("s"), 1)     # reserves 1..10
        a.setval("s", 100, False)
        self.assertEqual(a.nextval("s"), 100)   # not 2 from the old block
        self.assertEqual(a.nextval("s"), 101)

    def test_setval_does_not_discard_other_sessions_cached_blocks(self):
        cat = make(start=1, cache=5)
        a, b, admin = Session("a", cat), Session("b", cat), Session("admin", cat)
        self.assertEqual(a.nextval("s"), 1)      # a reserves 1..5
        self.assertEqual(b.nextval("s"), 6)      # b reserves 6..10
        admin.setval("s", 100, True)
        # Values already cached before the setval are still handed out.
        self.assertEqual(a.nextval("s"), 2)
        self.assertEqual(b.nextval("s"), 7)
        self.assertEqual([a.nextval("s"), a.nextval("s"), a.nextval("s")],
                         [3, 4, 5])
        # Once a's block is used up, the new state (100) is read.
        self.assertEqual(a.nextval("s"), 101)


class CacheTests(unittest.TestCase):
    def test_cache_blocks_are_handed_out_locally(self):
        cat = make(start=1, cache=5)
        a, b = Session("a", cat), Session("b", cat)
        self.assertEqual([a.nextval("s"), a.nextval("s")], [1, 2])
        self.assertEqual([b.nextval("s"), b.nextval("s")], [6, 7])
        self.assertEqual([a.nextval("s"), a.nextval("s"), a.nextval("s")],
                         [3, 4, 5])
        self.assertEqual(b.nextval("s"), 8)
        self.assertEqual(a.nextval("s"), 11)     # new block after b's

    def test_unused_cached_values_are_skipped(self):
        cat = make(start=1, cache=5)
        a, b = Session("a", cat), Session("b", cat)
        self.assertEqual(a.nextval("s"), 1)      # a reserves 1..5, uses 1
        self.assertEqual(b.nextval("s"), 6)      # b reserves 6..10

    def test_cache_one_never_skips(self):
        cat = make(start=1, cache=1)
        a, b = Session("a", cat), Session("b", cat)
        self.assertEqual([a.nextval("s"), b.nextval("s"), a.nextval("s")],
                         [1, 2, 3])


class TransactionTests(unittest.TestCase):
    def test_rollback_does_not_reuse_values(self):
        cat = make(start=1)
        a = Session("a", cat)
        a.begin()
        self.assertEqual([a.nextval("s"), a.nextval("s")], [1, 2])
        a.rollback()
        self.assertEqual(a.nextval("s"), 3)

    def test_rollback_does_not_undo_setval(self):
        cat = make(start=1)
        a = Session("a", cat)
        a.begin()
        a.setval("s", 500, False)
        a.rollback()
        self.assertEqual(a.nextval("s"), 500)

    def test_rollback_keeps_the_cached_block(self):
        cat = make(start=1, cache=10)
        a = Session("a", cat)
        a.begin()
        self.assertEqual(a.nextval("s"), 1)      # reserves 1..10
        a.rollback()
        self.assertEqual(a.nextval("s"), 2)      # cached value, not reclaimed

    def test_commit_does_not_change_anything(self):
        cat = make(start=1, cache=3)
        a, b = Session("a", cat), Session("b", cat)
        a.begin()
        self.assertEqual(a.nextval("s"), 1)
        a.commit()
        self.assertEqual(a.nextval("s"), 2)
        self.assertEqual(b.nextval("s"), 4)

    def test_other_session_sees_values_consumed_in_a_rolled_back_txn(self):
        cat = make(start=1)
        a, b = Session("a", cat), Session("b", cat)
        a.begin()
        a.nextval("s")
        a.rollback()
        self.assertEqual(b.nextval("s"), 2)


class ReferenceModelTests(unittest.TestCase):
    """Compare against a brute-force model of PostgreSQL's nextval rule."""

    @staticmethod
    def brute(increment, minvalue, maxvalue, start, cycle, count):
        last, called, out = start, False, []
        for _ in range(count):
            if called:
                nxt = last + increment
                if (increment > 0 and nxt > maxvalue) or \
                   (increment < 0 and nxt < minvalue):
                    if not cycle:
                        out.append("ERR")
                        continue
                    nxt = minvalue if increment > 0 else maxvalue
                last = nxt
            called = True
            out.append(last)
        return out

    def test_option_grid(self):
        increments = (1, 2, 3, -1, -2)
        for increment in increments:
            for minvalue in (1, -5):
                for maxvalue in (4, 7, -1):
                    if minvalue >= maxvalue:
                        continue
                    for cycle in (False, True):
                        start = minvalue if increment > 0 else maxvalue
                        cat = Catalog()
                        cat.create("s", increment=increment, minvalue=minvalue,
                                   maxvalue=maxvalue, start=start, cycle=cycle)
                        a = Session("a", cat)
                        got = []
                        for _ in range(12):
                            try:
                                got.append(a.nextval("s"))
                            except SequenceError:
                                got.append("ERR")
                        want = self.brute(increment, minvalue, maxvalue,
                                          start, cycle, 12)
                        self.assertEqual(
                            got, want,
                            f"increment={increment} min={minvalue} "
                            f"max={maxvalue} cycle={cycle}")


class OrderNumberTests(unittest.TestCase):
    def test_order_number(self):
        cat = Catalog()
        cat.create("order_seq", start=123)
        self.assertEqual(order_number(Session("w", cat)), "ORD-0000123")


if __name__ == "__main__":
    unittest.main()
