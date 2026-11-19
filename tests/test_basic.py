import unittest

from winx.engine import evaluate

ROWS = [{"id": i, "g": "a", "k": k, "v": v} for i, (k, v) in enumerate([(1, 10), (2, 20), (3, 30)], 1)]

# id, grp, day, amount (day ties, missing days and a NULL amount, two groups)
SALES = [
    {"id": 1, "g": "a", "k": 1, "v": 10},
    {"id": 2, "g": "a", "k": 2, "v": 20},
    {"id": 3, "g": "a", "k": 2, "v": 5},
    {"id": 4, "g": "a", "k": 4, "v": 7},
    {"id": 5, "g": "a", "k": 5, "v": None},
    {"id": 6, "g": "b", "k": 1, "v": 3},
    {"id": 7, "g": "b", "k": 3, "v": None},
    {"id": 8, "g": "b", "k": 3, "v": 8},
]


def bound(kind, offset=None):
    b = {"type": kind}
    if offset is not None:
        b["offset"] = offset
    return b


def window(mode=None, start=None, end=None, exclude="no_others", desc=False,
           by=None):
    w = {"partition_by": ["g"]}
    if by is not False:
        w["order_by"] = by or [{"col": "k", "dir": "desc" if desc else "asc"}]
    if mode is not None:
        w["frame"] = {"mode": mode, "start": start, "end": end,
                      "exclude": exclude}
    return w


def run(fn, w, arg="v", n=None):
    return evaluate(SALES, {"fn": fn, "arg": arg, "n": n, "window": w})


class BasicTests(unittest.TestCase):
    def test_running_sum_unique_keys(self):
        out = evaluate(ROWS, {"fn": "sum", "arg": "v", "window": {"partition_by": ["g"], "order_by": [{"col": "k"}]}})
        self.assertEqual(out, {1: 10, 2: 30, 3: 60})

    def test_rows_moving_sum(self):
        w = {"order_by": [{"col": "k"}], "frame": {"mode": "rows", "start": {"type": "preceding", "offset": 1},
                                                   "end": {"type": "current_row"}}}
        self.assertEqual(evaluate(ROWS, {"fn": "sum", "arg": "v", "window": w}), {1: 10, 2: 30, 3: 50})


class DefaultFrameTests(unittest.TestCase):
    """No frame clause: RANGE UNBOUNDED PRECEDING AND CURRENT ROW.

    Rows tied on the ordering column see the same (end-of-group) value.
    """

    def test_default_sum_stops_at_end_of_peer_group(self):
        self.assertEqual(run("sum", window()), {
            1: 10, 2: 35, 3: 35, 4: 42, 5: 42, 6: 3, 7: 11, 8: 11})

    def test_default_last_value_is_last_peer(self):
        self.assertEqual(run("last_value", window()), {
            1: 10, 2: 5, 3: 5, 4: 7, 5: None, 6: 3, 7: 8, 8: 8})

    def test_default_first_and_nth_value(self):
        self.assertEqual(run("first_value", window()), {
            1: 10, 2: 10, 3: 10, 4: 10, 5: 10, 6: 3, 7: 3, 8: 3})
        self.assertEqual(run("nth_value", window(), n=2), {
            1: None, 2: 20, 3: 20, 4: 20, 5: 20, 6: None, 7: None, 8: None})

    def test_no_order_by_covers_whole_partition(self):
        self.assertEqual(run("sum", window(by=False)), {
            1: 42, 2: 42, 3: 42, 4: 42, 5: 42, 6: 11, 7: 11, 8: 11})
        self.assertEqual(run("last_value", window(by=False)), {
            1: None, 2: None, 3: None, 4: None, 5: None, 6: 8, 7: 8, 8: 8})

    def test_default_desc(self):
        self.assertEqual(run("sum", window(desc=True)), {
            1: 42, 2: 32, 3: 32, 4: 7, 5: None, 6: 11, 7: 8, 8: 8})


class RowsModeTests(unittest.TestCase):
    def test_rows_offset_counts_physical_rows(self):
        w = window("rows", bound("preceding", 1), bound("current_row"))
        self.assertEqual(run("sum", w), {
            1: 10, 2: 30, 3: 25, 4: 12, 5: 7, 6: 3, 7: 3, 8: 8})

    def test_rows_full_partition(self):
        w = window("rows", bound("unbounded_preceding"), bound("unbounded_following"))
        # count(col) skips NULLs: group a has 4 amounts, group b has 2.
        self.assertEqual(run("count", w), {i: 4 if i <= 5 else 2 for i in range(1, 9)})
        self.assertEqual(run("count_star", w), {i: 5 if i <= 5 else 3 for i in range(1, 9)})


class RangeModeTests(unittest.TestCase):
    def test_range_offset_counts_value_distance(self):
        # "previous day to current day": both day-2 rows get 35, not 30/25.
        w = window("range", bound("preceding", 1), bound("current_row"))
        self.assertEqual(run("sum", w), {
            1: 10, 2: 35, 3: 35, 4: 7, 5: 7, 6: 3, 7: 8, 8: 8})

    def test_range_around_current_row(self):
        w = window("range", bound("preceding", 1), bound("following", 1))
        self.assertEqual(run("min", w), {
            1: 5, 2: 5, 3: 5, 4: 7, 5: 7, 6: 3, 7: 8, 8: 8})

    def test_range_desc_direction(self):
        w = window("range", bound("preceding", 1), bound("current_row"), desc=True)
        self.assertEqual(run("sum", w), {
            1: 35, 2: 25, 3: 25, 4: 7, 5: None, 6: 3, 7: 8, 8: 8})


class GroupsModeTests(unittest.TestCase):
    def test_groups_offset_counts_peer_groups(self):
        w = window("groups", bound("preceding", 1), bound("current_row"))
        self.assertEqual(run("sum", w), {
            1: 10, 2: 35, 3: 35, 4: 32, 5: 7, 6: 3, 7: 11, 8: 11})

    def test_groups_with_out_of_range_bounds(self):
        w = window("groups", bound("following", 5), bound("following", 9))
        self.assertEqual(run("count", w), {i: 0 for i in range(1, 9)})


class ExcludeTests(unittest.TestCase):
    def test_exclude_current_row(self):
        w = window("range", bound("unbounded_preceding"), bound("current_row"),
                   exclude="current_row")
        self.assertEqual(run("sum", w), {
            1: None, 2: 15, 3: 30, 4: 35, 5: 42, 6: None, 7: 11, 8: 3})

    def test_exclude_group(self):
        w = window("range", bound("unbounded_preceding"), bound("current_row"),
                   exclude="group")
        self.assertEqual(run("sum", w), {
            1: None, 2: 10, 3: 10, 4: 35, 5: 42, 6: None, 7: 3, 8: 3})

    def test_exclude_ties(self):
        w = window("range", bound("unbounded_preceding"), bound("current_row"),
                   exclude="ties")
        self.assertEqual(run("sum", w), {
            1: 10, 2: 30, 3: 15, 4: 42, 5: 42, 6: 3, 7: 3, 8: 11})

    def test_exclude_ties_in_rows_mode(self):
        w = window("rows", bound("unbounded_preceding"), bound("current_row"),
                   exclude="ties")
        self.assertEqual(run("count_star", w), {
            1: 1, 2: 2, 3: 2, 4: 4, 5: 5, 6: 1, 7: 2, 8: 2})


class ValueFunctionTests(unittest.TestCase):
    def test_first_last_nth_over_full_frame(self):
        w = window("rows", bound("unbounded_preceding"), bound("unbounded_following"))
        self.assertEqual(run("first_value", w), {
            1: 10, 2: 10, 3: 10, 4: 10, 5: 10, 6: 3, 7: 3, 8: 3})
        self.assertEqual(run("last_value", w), {
            1: None, 2: None, 3: None, 4: None, 5: None, 6: 8, 7: 8, 8: 8})
        self.assertEqual(run("nth_value", w, n=2), {
            1: 20, 2: 20, 3: 20, 4: 20, 5: 20, 6: None, 7: None, 8: None})

    def test_avg_and_count(self):
        w = window("rows", bound("preceding", 1), bound("following", 1))
        # count(v) skips the two NULL amounts, count_star counts every row.
        self.assertEqual(run("count", w), {
            1: 2, 2: 3, 3: 3, 4: 2, 5: 1, 6: 1, 7: 2, 8: 1})
        self.assertEqual(run("count_star", w), {
            1: 2, 2: 3, 3: 3, 4: 3, 5: 2, 6: 2, 7: 3, 8: 2})
        self.assertAlmostEqual(run("avg", w)[2], 35 / 3)


class RankingTests(unittest.TestCase):
    def test_ranking_with_ties(self):
        w = window()
        self.assertEqual(run("rank", w), {
            1: 1, 2: 2, 3: 2, 4: 4, 5: 5, 6: 1, 7: 2, 8: 2})
        self.assertEqual(run("dense_rank", w), {
            1: 1, 2: 2, 3: 2, 4: 3, 5: 4, 6: 1, 7: 2, 8: 2})
        self.assertEqual(run("row_number", w), {
            1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 1, 7: 2, 8: 3})


class FrameValidationTests(unittest.TestCase):
    def test_range_offset_needs_single_order_column(self):
        w = {"order_by": [{"col": "k"}, {"col": "v"}],
             "frame": {"mode": "range", "start": bound("preceding", 1),
                       "end": bound("current_row")}}
        with self.assertRaises(ValueError):
            run("sum", w)

    def test_groups_needs_order_by(self):
        w = {"partition_by": ["g"],
             "frame": {"mode": "groups", "start": bound("preceding", 1),
                       "end": bound("current_row")}}
        with self.assertRaises(ValueError):
            run("sum", w)

    def test_start_cannot_be_unbounded_following(self):
        w = window("rows", bound("unbounded_following"), bound("unbounded_following"))
        with self.assertRaises(ValueError):
            run("sum", w)

    def test_end_cannot_be_unbounded_preceding(self):
        w = window("rows", bound("unbounded_preceding"), bound("unbounded_preceding"))
        with self.assertRaises(ValueError):
            run("sum", w)

    def test_following_start_cannot_end_at_current_row(self):
        w = window("rows", bound("following", 1), bound("current_row"))
        with self.assertRaises(ValueError):
            run("sum", w)

    def test_current_row_start_cannot_have_preceding_end(self):
        w = window("rows", bound("current_row"), bound("preceding", 1))
        with self.assertRaises(ValueError):
            run("sum", w)

    def test_negative_offset_rejected(self):
        w = window("rows", bound("preceding", -1), bound("current_row"))
        with self.assertRaises(ValueError):
            run("sum", w)


if __name__ == "__main__":
    unittest.main()
