import unittest

from mjoin.inputs import SortedInput
from mjoin.mergejoin import merge_join
from mjoin.nestloop import nested_loop
from mjoin.planner import join

L = [{"id": f"l{i}", "k": i} for i in range(1, 21)]
R = [{"id": f"r{i}", "k": i * 2} for i in range(1, 15)]

# Duplicate keys on both sides, gaps on both sides and NULLs on both sides.
DUP_L = [
    {"id": "l1", "k": 1}, {"id": "l2", "k": 2}, {"id": "l3", "k": 2},
    {"id": "l4", "k": 2}, {"id": "l5", "k": 3}, {"id": "l6", "k": None},
    {"id": "l7", "k": 5}, {"id": "l8", "k": 5}, {"id": "l9", "k": None},
    {"id": "l10", "k": 7},
]
DUP_R = [
    {"id": "r1", "k": 2}, {"id": "r2", "k": 2}, {"id": "r3", "k": 3},
    {"id": "r4", "k": 4}, {"id": "r5", "k": None}, {"id": "r6", "k": 5},
    {"id": "r7", "k": 5}, {"id": "r8", "k": 5}, {"id": "r9", "k": 8},
]

# Composite keys incl. a NULL in one key column, plus an extra join condition.
ASOF_L = [
    {"id": "s1", "region": "eu", "sku": 1, "day": 3},
    {"id": "s2", "region": "eu", "sku": 1, "day": 9},
    {"id": "s3", "region": "eu", "sku": None, "day": 4},
    {"id": "s4", "region": "us", "sku": 2, "day": 5},
    {"id": "s5", "region": "us", "sku": 2, "day": 1},
    {"id": "s6", "region": "us", "sku": 4, "day": 1},
    {"id": "s7", "region": "zz", "sku": 9, "day": 1},
]
ASOF_R = [
    {"id": "p1", "region": "eu", "sku": 1, "valid_from": 1},
    {"id": "p2", "region": "eu", "sku": 1, "valid_from": 8},
    {"id": "p3", "region": "us", "sku": 2, "valid_from": 4},
    {"id": "p4", "region": "eu", "sku": None, "valid_from": 0},
    {"id": "p5", "region": "us", "sku": 3, "valid_from": 0},
]
ASOF_FILTER = [{"l": "day", "op": ">=", "r": "valid_from"}]

KINDS = ("inner", "left", "right", "full", "semi", "anti")


def _norm(rows):
    return sorted(rows, key=lambda t: (str(t[0]), str(t[1])))


def _merge(rows_l, rows_r, lkey, rkey, kind, filters=None):
    left, right = SortedInput(list(rows_l), lkey), SortedInput(list(rows_r), rkey)
    out = merge_join(left, right, kind, filters)
    return out, left, right


class JoinTests(unittest.TestCase):
    def test_inner_unique_keys(self):
        out = join(L, R, "k", "k")
        self.assertEqual(out["method"], "merge join")
        self.assertEqual(sorted(out["rows"]), sorted((f"l{i}", f"r{i // 2}") for i in range(2, 21, 2)))

    def test_left_join_keeps_unmatched(self):
        out = join(L, R, "k", "k", "left")
        self.assertEqual(len(out["rows"]), 20)

    def test_tiny_inputs_use_nested_loop(self):
        out = join(L[:3], R[:3], "k", "k")
        self.assertEqual(out["method"], "nested loop")

    def test_duplicate_keys_match_nested_loop_every_kind(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                got, left, right = _merge(DUP_L, DUP_R, "k", "k", kind)
                want = nested_loop(DUP_L, DUP_R, "k", "k", kind)
                self.assertEqual(_norm(got), _norm(want))
                # Each input row is fetched exactly once: no rewinding.
                self.assertEqual(left.fetches, len(DUP_L))
                self.assertEqual(right.fetches, len(DUP_R))

    def test_null_keys_never_match(self):
        got, _, _ = _merge(DUP_L, DUP_R, "k", "k", "inner")
        self.assertNotIn(("l6", "r5"), got)
        self.assertNotIn(("l9", "r5"), got)
        left_rows, _, _ = _merge(DUP_L, DUP_R, "k", "k", "left")
        self.assertIn(("l6", None), left_rows)
        self.assertIn(("l9", None), left_rows)
        full_rows, _, _ = _merge(DUP_L, DUP_R, "k", "k", "full")
        self.assertIn((None, "r5"), full_rows)

    def test_duplicate_run_is_a_cross_product(self):
        # 3 left rows * 2 right rows on key 2, 2 * 3 on key 5.
        got, _, _ = _merge(DUP_L, DUP_R, "k", "k", "inner")
        self.assertEqual(len(got), 6 + 1 + 6)
        self.assertEqual(
            sorted(p for p in got if p[0] in ("l2", "l3", "l4")),
            sorted((l, r) for l in ("l2", "l3", "l4") for r in ("r1", "r2")),
        )

    def test_semi_and_anti_are_complements(self):
        semi, _, _ = _merge(DUP_L, DUP_R, "k", "k", "semi")
        anti, _, _ = _merge(DUP_L, DUP_R, "k", "k", "anti")
        semi_ids = {t[0] for t in semi}
        anti_ids = {t[0] for t in anti}
        self.assertEqual(semi_ids & anti_ids, set())
        self.assertEqual(semi_ids | anti_ids, {row["id"] for row in DUP_L})
        # One row per left row at most.
        self.assertEqual(len(semi), len(semi_ids))

    def test_composite_keys_with_filter_match_nested_loop(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                got, left, right = _merge(ASOF_L, ASOF_R, ["region", "sku"], ["region", "sku"],
                                          kind, ASOF_FILTER)
                want = nested_loop(ASOF_L, ASOF_R, ["region", "sku"], ["region", "sku"], kind, ASOF_FILTER)
                self.assertEqual(_norm(got), _norm(want))
                self.assertEqual(left.fetches, len(ASOF_L))
                self.assertEqual(right.fetches, len(ASOF_R))

    def test_filter_failure_keeps_outer_rows_and_marks_right_unmatched(self):
        # s4/day=5 passes p3/valid_from=4, s5/day=1 fails; p3 still gets a match.
        got, _, _ = _merge(ASOF_L, ASOF_R, ["region", "sku"], ["region", "sku"],
                           "left", ASOF_FILTER)
        self.assertIn(("s4", "p3"), got)
        self.assertIn(("s5", None), got)
        full, _, _ = _merge(ASOF_L, ASOF_R, ["region", "sku"], ["region", "sku"],
                            "full", ASOF_FILTER)
        # p5 has no left row with the same composite key at all.
        self.assertIn((None, "p5"), full)

    def test_empty_inputs(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(_merge([], DUP_R, "k", "k", kind)[0],
                                 nested_loop([], DUP_R, "k", "k", kind))
                self.assertEqual(_merge(DUP_L, [], "k", "k", kind)[0],
                                 nested_loop(DUP_L, [], "k", "k", kind))


if __name__ == "__main__":
    unittest.main()
