import unittest

from orderidx.btree import BTree
from orderidx.planner import execute, plan
from orderidx.sortkeys import compare_rows, normalize, sort_rows

ROWS = [{"id": i, "a": v} for i, v in enumerate([5, 3, 9, 1], 1)]

# Rows with NULLs: a-values are [None, 1, None, 2]
NULL_ROWS = [
    {"id": 1, "a": None, "b": 2},
    {"id": 2, "a": 1, "b": None},
    {"id": 3, "a": None, "b": 1},
    {"id": 4, "a": 2, "b": 3},
]


class NormalizeTests(unittest.TestCase):
    def test_default_null_placement_matches_postgresql(self):
        self.assertEqual(normalize({"col": "a"}),
                         {"col": "a", "dir": "asc", "nulls": "last"})
        self.assertEqual(normalize({"col": "a", "dir": "asc"}),
                         {"col": "a", "dir": "asc", "nulls": "last"})
        self.assertEqual(normalize({"col": "a", "dir": "desc"}),
                         {"col": "a", "dir": "desc", "nulls": "first"})

    def test_explicit_null_placement_is_kept(self):
        self.assertEqual(normalize({"col": "a", "dir": "desc", "nulls": "last"})["nulls"], "last")
        self.assertEqual(normalize({"col": "a", "dir": "asc", "nulls": "first"})["nulls"], "first")


class SortNullTests(unittest.TestCase):
    def test_asc_default_nulls_last(self):
        out = sort_rows(NULL_ROWS, [{"col": "a"}])
        self.assertEqual([r["id"] for r in out], [2, 4, 1, 3])

    def test_desc_default_nulls_first(self):
        out = sort_rows(NULL_ROWS, [{"col": "a", "dir": "desc"}])
        # NULLs first (tied), then 2, then 1
        self.assertEqual([r["id"] for r in out[:2]], [1, 3])
        self.assertEqual([r["id"] for r in out[2:]], [4, 2])

    def test_explicit_nulls_last_with_desc(self):
        out = sort_rows(NULL_ROWS, [{"col": "a", "dir": "desc", "nulls": "last"}])
        self.assertEqual([r["id"] for r in out], [4, 2, 1, 3])

    def test_null_comparison_is_independent_of_direction(self):
        null_row, val_row = NULL_ROWS[0], NULL_ROWS[1]
        # NULLS FIRST: NULL sorts before values under either direction.
        self.assertEqual(compare_rows(null_row, val_row, [{"col": "a", "nulls": "first"}]), -1)
        self.assertEqual(compare_rows(null_row, val_row, [{"col": "a", "dir": "desc", "nulls": "first"}]), -1)
        # NULLS LAST: NULL sorts after values under either direction.
        self.assertEqual(compare_rows(null_row, val_row, [{"col": "a", "nulls": "last"}]), 1)
        self.assertEqual(compare_rows(null_row, val_row, [{"col": "a", "dir": "desc", "nulls": "last"}]), 1)


class IndexMatchTests(unittest.TestCase):
    def _ix(self, col, **kw):
        return BTree("ix", [{"col": col, **kw}]).build(NULL_ROWS)

    def test_backward_scan_reverses_null_placement(self):
        # ASC NULLS LAST index, read backwards -> DESC NULLS FIRST (PG default).
        ix = self._ix("a")
        out = execute({"order_by": [{"col": "a", "dir": "desc"}], "limit": 2}, NULL_ROWS, [ix])
        self.assertEqual(out["path"], "index scan ix backward")
        self.assertEqual(set(out["ids"]), {1, 3})  # the two NULL rows

    def test_explicit_nulls_last_cannot_use_desc_nulls_first_index(self):
        # DESC index defaults to NULLS FIRST; DESC NULLS LAST cannot be served
        # in either scan direction, so a sort is required.
        ix = self._ix("a", dir="desc")
        q = {"order_by": [{"col": "a", "dir": "desc", "nulls": "last"}]}
        self.assertEqual(plan(q, [ix])["path"], "seq scan + sort")
        out = execute(q, NULL_ROWS, [ix])
        self.assertEqual(out["ids"], [4, 2, 1, 3])

    def test_asc_nulls_last_uses_desc_index_backwards(self):
        ix = self._ix("a", dir="desc")  # DESC NULLS FIRST
        q = {"order_by": [{"col": "a", "nulls": "last"}]}  # ASC NULLS LAST
        self.assertEqual(plan(q, [ix])["path"], "index scan ix backward")

    def test_nulls_first_asc_cannot_use_default_asc_index(self):
        ix = self._ix("a")  # ASC NULLS LAST
        q = {"order_by": [{"col": "a", "nulls": "first"}]}  # ASC NULLS FIRST
        self.assertEqual(plan(q, [ix])["path"], "seq scan + sort")

    def test_multi_column_mixed_order_backward(self):
        ix = BTree("ix2", [
            {"col": "a", "dir": "asc", "nulls": "last"},
            {"col": "b", "dir": "desc", "nulls": "first"},
        ]).build(NULL_ROWS)
        # Reversing the index flips every column: DESC NULLS FIRST, ASC NULLS LAST.
        q = {"order_by": [
            {"col": "a", "dir": "desc", "nulls": "first"},
            {"col": "b", "dir": "asc", "nulls": "last"},
        ]}
        self.assertEqual(plan(q, [ix])["path"], "index scan ix2 backward")
        # Same columns but b wants NULLS FIRST: no scan direction fits -> sort.
        q_bad = {"order_by": [
            {"col": "a", "dir": "desc", "nulls": "first"},
            {"col": "b", "dir": "asc", "nulls": "first"},
        ]}
        self.assertEqual(plan(q_bad, [ix])["path"], "seq scan + sort")

    def test_equality_prefix_with_null_value(self):
        rows = NULL_ROWS + [{"id": 5, "a": None, "b": 0}]
        ix = BTree("ix", [{"col": "a"}, {"col": "b"}]).build(rows)
        out = execute({"where_eq": {"a": None}, "order_by": [{"col": "b"}]}, rows, [ix])
        self.assertEqual(out["path"], "index scan ix")
        self.assertEqual(out["ids"], [5, 3, 1])  # b = 0, 1, 2


class PagingTests(unittest.TestCase):
    def test_offset_applies_on_index_scan(self):
        ix = BTree("ix", [{"col": "a"}]).build(ROWS)
        full = execute({"order_by": [{"col": "a"}]}, ROWS, [ix])["ids"]
        paged = execute({"order_by": [{"col": "a"}], "offset": 1, "limit": 2}, ROWS, [ix])
        self.assertEqual(paged["path"], "index scan ix")
        self.assertEqual(paged["ids"], full[1:3])

    def test_offset_past_the_end_is_empty(self):
        ix = BTree("ix", [{"col": "a"}]).build(ROWS)
        out = execute({"order_by": [{"col": "a"}], "offset": 99, "limit": 2}, ROWS, [ix])
        self.assertEqual(out["ids"], [])

    def test_with_ties_includes_equal_key_rows(self):
        rows = [{"id": 1, "a": 1}, {"id": 2, "a": 1}, {"id": 3, "a": 1},
                {"id": 4, "a": 2}, {"id": 5, "a": 2}]
        ix = BTree("ix", [{"col": "a"}]).build(rows)
        out = execute({"order_by": [{"col": "a"}], "limit": 1, "with_ties": True}, rows, [ix])
        self.assertEqual(out["path"], "index scan ix")
        self.assertEqual(out["ids"], [1, 2, 3])  # all a=1 rows pulled in by ties

    def test_without_ties_stops_at_limit(self):
        rows = [{"id": 1, "a": 1}, {"id": 2, "a": 1}, {"id": 3, "a": 2}]
        ix = BTree("ix", [{"col": "a"}]).build(rows)
        out = execute({"order_by": [{"col": "a"}], "limit": 1}, rows, [ix])
        self.assertEqual(out["ids"], [1])

    def test_with_ties_on_sort_path(self):
        rows = [{"id": 1, "a": 1}, {"id": 2, "a": 1}, {"id": 3, "a": 2}]
        out = execute({"order_by": [{"col": "a", "nulls": "first"}],
                       "limit": 1, "with_ties": True}, rows, [])
        self.assertEqual(out["path"], "seq scan + sort")
        self.assertEqual(out["ids"], [1, 2])


class BasicTests(unittest.TestCase):
    def test_index_ascending(self):
        ix = BTree("ix", [{"col": "a"}]).build(ROWS)
        out = execute({"order_by": [{"col": "a"}], "limit": 2}, ROWS, [ix])
        self.assertEqual(out, {"path": "index scan ix", "ids": [4, 2]})

    def test_sort_without_index(self):
        out = execute({"order_by": [{"col": "a", "dir": "desc"}]}, ROWS, [])
        self.assertEqual(out["ids"], [3, 1, 2, 4])


if __name__ == "__main__":
    unittest.main()
