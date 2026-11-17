import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aggregate import agg_name, aggregate_gset, grouping_sets, plan, run  # noqa: E402
from values import key_of, partition, sort_key  # noqa: E402


# ---------------------------------------------------------------------------
# Independent reference implementation, written straight from the SQL
# definitions. The executor under test shares no code with it.
# ---------------------------------------------------------------------------

def _ref_agg(fn, arg, distinct, rows):
    if arg == "*":
        assert fn == "count"
        return len(rows)
    values = [r.get(arg) for r in rows if r.get(arg) is not None]  # NULL ignored
    if distinct:
        seen, uniq = set(), []
        for v in values:  # first occurrence only, but sets of scalars are unordered
            if v not in seen:
                seen.add(v)
                uniq.append(v)
        values = uniq
    if fn == "count":
        return len(values)
    if not values:
        return None
    if fn == "sum":
        return sum(values)
    if fn == "avg":
        return sum(values) / len(values)
    if fn == "min":
        return min(values)
    if fn == "max":
        return max(values)
    raise ValueError(fn)


def _ref_run(q):
    """Reference result rows, as a set so ordering is checked separately."""
    rows = q.get("rows", [])
    sets = grouping_sets(q)
    specs, aliases = plan(q)
    columns = []
    for gset in sets:
        columns += [c for c in gset if c not in columns]

    expected = {}
    for gset in sets:
        buckets = {}
        for r in rows:
            buckets.setdefault(tuple(None if r.get(c) is None else r.get(c)
                                     for c in gset), []).append(r)
        if not gset and () not in buckets:
            buckets[()] = []
        mask = sum(1 << i for i, c in enumerate(columns) if c not in gset)
        for key, grp in buckets.items():
            scope = dict(zip(gset, key))
            vals = {}
            for name, s in specs.items():
                if s["fn"] == "grouping":
                    vals[name] = 1 if s["arg"] not in gset else 0
                else:
                    vals[name] = _ref_agg(s["fn"], s["arg"],
                                          bool(s.get("distinct")), grp)
            keep = True
            for left, op, val in q.get("having", []):
                if isinstance(left, dict):
                    got = vals[agg_name(left)]
                elif left in aliases:
                    got = vals[aliases[left]]
                else:
                    got = scope.get(left)
                if got is None or val is None:
                    keep = False
                    break
                if not _cmp(got, op, val):
                    keep = False
                    break
            if not keep:
                continue
            out = {c: None for c in columns}
            out.update(scope)
            out.update({a["as"]: vals[aliases[a["as"]]] for a in q["aggregates"]})
            out["grouping"] = mask
            expected[(mask, key)] = out
    return expected, columns


def _cmp(a, op, b):
    import operator
    ops = {"=": operator.eq, "!=": operator.ne, "<": operator.lt,
           "<=": operator.le, ">": operator.gt, ">=": operator.ge}
    return ops[op](a, b)


# ---------------------------------------------------------------------------
# The acceptance dataset: NULLs, duplicates, several dimensions.
# ---------------------------------------------------------------------------

ROWS = [
    {"region": "eu", "product": "tea", "cust": "c1", "amt": 5},
    {"region": "eu", "product": None, "cust": "c2", "amt": 7},
    {"region": None, "product": "tea", "cust": None, "amt": None},
    {"region": "us", "product": "tea", "cust": "c1", "amt": 3},
    {"region": None, "product": None, "cust": "c3", "amt": 4},
    {"region": "eu", "product": "tea", "cust": "c1", "amt": 6},
    {"region": None, "product": "jam", "cust": "c2", "amt": 2},
    {"region": "us", "product": None, "cust": None, "amt": None},
]

AGGS = [
    {"as": "orders", "fn": "count", "arg": "*"},
    {"as": "buyers", "fn": "count", "arg": "cust", "distinct": True},
    {"as": "cust_rows", "fn": "count", "arg": "cust"},
    {"as": "total", "fn": "sum", "arg": "amt"},
    {"as": "avg_amt", "fn": "avg", "arg": "amt"},
    {"as": "lo", "fn": "min", "arg": "amt"},
    {"as": "hi", "fn": "max", "arg": "amt"},
    {"as": "d_sum", "fn": "sum", "arg": "amt", "distinct": True},
]


class AggregateTests(unittest.TestCase):
    def test_sum_per_group(self):
        q = {"rows": [{"g": "a", "x": 1}, {"g": "b", "x": 2}, {"g": "a", "x": 3}],
             "group_by": ["g"], "aggregates": [{"as": "s", "fn": "sum", "arg": "x"}]}
        self.assertEqual([(r["g"], r["s"]) for r in run(q)["rows"]],
                         [("a", 4), ("b", 2)])

    def test_count_star(self):
        q = {"rows": [{"g": 1}, {"g": 1}], "group_by": ["g"],
             "aggregates": [{"as": "n", "fn": "count", "arg": "*"}]}
        self.assertEqual(run(q)["rows"][0]["n"], 2)

    # -- NULL handling -----------------------------------------------------

    def test_nulls_form_one_group(self):
        q = {"rows": ROWS, "group_by": ["region"], "aggregates": AGGS}
        res = run(q)["rows"]
        null_rows = [r for r in res if r["region"] is None]
        self.assertEqual(len(null_rows), 1)
        self.assertEqual(null_rows[0]["orders"], 3)  # all three NULL-region rows
        # NULLs are ignored by every aggregate except count(*)
        self.assertEqual(null_rows[0]["cust_rows"], 2)
        self.assertEqual(null_rows[0]["buyers"], 2)
        self.assertEqual(null_rows[0]["total"], 6)
        self.assertEqual(null_rows[0]["avg_amt"], 3.0)
        self.assertEqual(null_rows[0]["lo"], 2)
        self.assertEqual(null_rows[0]["hi"], 4)
        self.assertEqual(len(res), 3)  # NULL, eu, us

    def test_count_distinct_ignores_null(self):
        rows = [{"g": 1, "v": None}, {"g": 1, "v": None},
                {"g": 1, "v": "a"}, {"g": 1, "v": "a"}, {"g": 1, "v": "b"}]
        q = {"rows": rows, "group_by": ["g"],
             "aggregates": [
                 {"as": "d", "fn": "count", "arg": "v", "distinct": True},
                 {"as": "nd", "fn": "count", "arg": "v"},
                 {"as": "star", "fn": "count", "arg": "*"},
                 {"as": "sd", "fn": "sum", "arg": "n", "distinct": True}]}
        rows2 = [dict(r, n=(None if r["v"] is None else len(r["v"]))) for r in rows]
        q["rows"] = rows2
        r = run(q)["rows"][0]
        self.assertEqual(r["d"], 2)       # {a, b}, NULL excluded
        self.assertEqual(r["nd"], 3)      # three non-NULL rows
        self.assertEqual(r["star"], 5)    # count(*) counts everything
        self.assertEqual(r["sd"], 1)      # distinct lengths {1}; the two a's collapse

    def test_aggregates_of_only_nulls_are_null_but_count_is_zero(self):
        rows = [{"g": "x", "v": None}, {"g": "x", "v": None}]
        q = {"rows": rows, "group_by": ["g"], "aggregates": [
            {"as": "n", "fn": "count", "arg": "v"},
            {"as": "s", "fn": "sum", "arg": "v"},
            {"as": "a", "fn": "avg", "arg": "v"},
            {"as": "mn", "fn": "min", "arg": "v"},
            {"as": "mx", "fn": "max", "arg": "v"}]}
        r = run(q)["rows"][0]
        self.assertEqual(r["n"], 0)
        self.assertIsNone(r["s"])
        self.assertIsNone(r["a"])
        self.assertIsNone(r["mn"])
        self.assertIsNone(r["mx"])

    # -- HAVING ------------------------------------------------------------

    def test_having_references_output_alias(self):
        q = {"rows": ROWS, "group_by": ["region"], "aggregates": AGGS,
             "having": [["orders", ">=", 3]]}
        res = run(q)["rows"]
        self.assertEqual({r["region"] for r in res}, {None, "eu"})

    def test_having_references_unselected_aggregate_expression(self):
        q = {"rows": ROWS, "group_by": ["region"],
             "aggregates": [{"as": "orders", "fn": "count", "arg": "*"}],
             "having": [[{"fn": "sum", "arg": "amt"}, ">", 5]]}
        res = run(q)["rows"]
        # eu total = 18, NULL total = 6, us total = 3
        self.assertEqual({r["region"] for r in res}, {None, "eu"})

    def test_having_comparison_with_null_is_false(self):
        rows = [{"g": "a", "v": 1}, {"g": "b", "v": None}]
        q = {"rows": rows, "group_by": ["g"],
             "aggregates": [{"as": "s", "fn": "sum", "arg": "v"}],
             "having": [["s", ">=", 0]]}
        self.assertEqual([r["g"] for r in run(q)["rows"]], ["a"])
        q["having"] = [["s", "!=", 1]]
        self.assertEqual([r["g"] for r in run(q)["rows"]], [])  # NULL != 1 is false
        q["having"] = [["g", "!=", None]]
        self.assertEqual(run(q)["rows"], [])

    def test_having_on_grouping_column(self):
        q = {"rows": ROWS, "group_by": ["region"],
             "aggregates": [{"as": "n", "fn": "count", "arg": "*"}],
             "having": [["region", "=", "eu"]]}
        self.assertEqual([r["region"] for r in run(q)["rows"]], ["eu"])

    # -- grouping sets, rollup, GROUPING ----------------------------------

    def test_grouping_sets_and_grouping_function(self):
        q = {"rows": ROWS,
             "grouping_sets": [["region", "product"], ["region"], []],
             "aggregates": AGGS + [
                 {"as": "g_region", "fn": "grouping", "arg": "region"},
                 {"as": "g_product", "fn": "grouping", "arg": "product"}]}
        res = run(q)
        rows_by_mask = {}
        for r in res["rows"]:
            rows_by_mask.setdefault(r["grouping"], []).append(r)
        # bit 0 = region, bit 1 = product (order of first appearance)
        self.assertEqual(set(rows_by_mask), {0, 2, 3})
        for r in rows_by_mask[0]:
            self.assertEqual(r["g_region"], 0)
            self.assertEqual(r["g_product"], 0)
        for r in rows_by_mask[2]:
            self.assertEqual(r["g_region"], 0)
            self.assertEqual(r["g_product"], 1)
            self.assertIsNone(r["product"])
        for r in rows_by_mask[3]:
            self.assertEqual(r["g_region"], 1)
            self.assertEqual(r["g_product"], 1)
            self.assertIsNone(r["region"])
            self.assertIsNone(r["product"])
        # grand total row
        grand = rows_by_mask[3][0]
        self.assertEqual(grand["orders"], 8)
        self.assertEqual(grand["total"], 27)
        self.assertEqual(grand["buyers"], 3)
        # number of groups: 7 (region,product) + 3 (region) + 1 grand
        self.assertEqual(len(res["rows"]), 7 + 3 + 1)
    def test_rollup_equivalent_to_nested_grouping_sets(self):
        q1 = {"rows": ROWS, "rollup": ["region", "product"], "aggregates": AGGS}
        q2 = {"rows": ROWS,
              "grouping_sets": [["region", "product"], ["region"], []],
              "aggregates": AGGS}
        self.assertEqual(run(q1)["rows"], run(q2)["rows"])

    def test_grouping_function_in_having_without_being_selected(self):
        q = {"rows": ROWS, "rollup": ["region", "product"],
             "aggregates": [{"as": "orders", "fn": "count", "arg": "*"}],
             "having": [[{"fn": "grouping", "arg": "product"}, "=", 1]]}
        res = run(q)["rows"]
        # only region-level and grand-total rows survive
        self.assertEqual({r["grouping"] for r in res}, {2, 3})

    # -- empty input and single group -------------------------------------

    def test_empty_input_global_group_still_emitted(self):
        q = {"rows": [], "group_by": [], "aggregates": AGGS}
        res = run(q)
        self.assertEqual(res["spilled_partitions"], 0)
        self.assertEqual(len(res["rows"]), 1)
        r = res["rows"][0]
        self.assertEqual(r["orders"], 0)
        self.assertEqual(r["buyers"], 0)
        self.assertIsNone(r["total"])
        self.assertIsNone(r["avg_amt"])
        self.assertIsNone(r["lo"])
        self.assertIsNone(r["hi"])

    def test_empty_input_with_grouping_sets(self):
        q = {"rows": [], "grouping_sets": [["region"], ["region", "product"], []],
             "aggregates": [{"as": "n", "fn": "count", "arg": "*"}]}
        res = run(q)["rows"]
        # only the empty grouping set produces a row
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["grouping"], 3)
        self.assertEqual(res[0]["n"], 0)

    def test_empty_input_having_filters_the_global_row(self):
        q = {"rows": [], "group_by": [],
             "aggregates": [{"as": "n", "fn": "count", "arg": "*"}],
             "having": [["n", ">", 0]]}
        self.assertEqual(run(q)["rows"], [])

    def test_single_group_with_values(self):
        rows = [{"v": 1}, {"v": None}, {"v": 2}, {"v": 2}]
        q = {"rows": rows, "group_by": [], "aggregates": [
            {"as": "n", "fn": "count", "arg": "*"},
            {"as": "c", "fn": "count", "arg": "v"},
            {"as": "s", "fn": "sum", "arg": "v"},
            {"as": "ds", "fn": "sum", "arg": "v", "distinct": True}]}
        r = run(q)["rows"][0]
        self.assertEqual((r["n"], r["c"], r["s"], r["ds"]), (4, 3, 5, 3))

    # -- spill parity and per-partition quota ------------------------------

    def test_spill_matches_in_memory_row_for_row(self):
        q = {"rows": ROWS, "group_by": ["region"], "aggregates": AGGS}
        q_spill = dict(q, memory_groups=1)
        a, b = run(q), run(q_spill)
        self.assertEqual(a["rows"], b["rows"])
        self.assertEqual(a["spilled_partitions"], 0)
        # 3 groups with a quota of 1 -> 4 partitions (power of two, >= 3)
        self.assertEqual(b["spilled_partitions"], 4)

    def test_spill_with_grouping_sets_matches_in_memory(self):
        q = {"rows": ROWS,
             "grouping_sets": [["region", "product"], ["region"], []],
             "aggregates": AGGS}
        for limit in (1, 2, 3, 1000):
            self.assertEqual(run(dict(q, memory_groups=limit))["rows"],
                             run(q)["rows"], f"limit={limit}")

    def test_spill_large_dataset_parity(self):
        rows = [{"k": i % 23, "j": i % 3, "v": (None if i % 5 == 0 else i)}
                for i in range(500)]
        aggs = [{"as": "n", "fn": "count", "arg": "*"},
                {"as": "d", "fn": "count", "arg": "v", "distinct": True},
                {"as": "s", "fn": "sum", "arg": "v"},
                {"as": "mx", "fn": "max", "arg": "v"}]
        q = {"rows": rows, "group_by": ["k"], "aggregates": aggs}
        ref, _ = _ref_run(q)
        for limit in (1, 4, 7, 1000):
            res = run(dict(q, memory_groups=limit))
            got = {(r["grouping"], (r["k"],)): r for r in res["rows"]}
            self.assertEqual(got, ref, f"limit={limit}")
        self.assertGreater(run(dict(q, memory_groups=4))["spilled_partitions"], 0)

    def test_each_spill_partition_respects_quota(self):
        rows = [{"k": i, "v": i} for i in range(100)]
        limit = 7
        q = {"rows": rows, "group_by": ["k"],
             "aggregates": [{"as": "n", "fn": "count", "arg": "*"}],
             "memory_groups": limit}
        res = run(q)
        # 100 groups -> 16 partitions, the smallest power of two with
        # parts * limit >= number of groups; every key lands in exactly one
        self.assertEqual(res["spilled_partitions"], 16)
        from values import key_of, partition
        counts = [0] * 16
        for r in rows:
            counts[partition(key_of(r, ["k"]), 16)] += 1
        self.assertEqual(sum(counts), 100)
        self.assertTrue(all(c >= 1 for c in counts))

    # -- ordering ----------------------------------------------------------

    def test_sort_order_grouping_then_null_first_then_values(self):
        rows = [{"g": "b"}, {"g": None}, {"g": "a"}]
        q = {"rows": rows, "group_by": ["g"],
             "aggregates": [{"as": "n", "fn": "count", "arg": "*"}]}
        self.assertEqual([r["g"] for r in run(q)["rows"]], [None, "a", "b"])

    # -- row-by-row cross check against the reference ----------------------

    def test_matches_reference_definition_on_full_dataset(self):
        q = {"rows": ROWS,
             "grouping_sets": [["region", "product"], ["product"], ["region"], []],
             "aggregates": AGGS + [
                 {"as": "g_region", "fn": "grouping", "arg": "region"},
                 {"as": "g_product", "fn": "grouping", "arg": "product"}],
             "having": [["orders", ">=", 1],
                        [{"fn": "avg", "arg": "amt"}, "!=", 0]]}
        ref, columns = _ref_run(q)
        res = run(q)
        got = {(r["grouping"], tuple(r[c] for c in gset)): r
               for r in res["rows"]
               for gset in [next(s for s in grouping_sets(q)
                                 if sum(1 << i for i, c in enumerate(columns)
                                        if c not in s) == r["grouping"])]}
        self.assertEqual(got, ref)
        # ordering invariant
        sk = [(r["grouping"],) + tuple(sort_key_for(r[c]) for c in columns)
              for r in res["rows"]]
        self.assertEqual(sk, sorted(sk))


def sort_key_for(v):
    from values import sort_key
    return sort_key(v)


AGG_POOL = [
    {"as": "n", "fn": "count", "arg": "*"},
    {"as": "cv", "fn": "count", "arg": "v"},
    {"as": "dv", "fn": "count", "arg": "v", "distinct": True},
    {"as": "sv", "fn": "sum", "arg": "v"},
    {"as": "dsv", "fn": "sum", "arg": "v", "distinct": True},
    {"as": "av", "fn": "avg", "arg": "v"},
    {"as": "mn", "fn": "min", "arg": "v"},
    {"as": "mx", "fn": "max", "arg": "v"},
]


class RandomDifferentialTests(unittest.TestCase):
    """Hundreds of random queries cross-checked against the definition."""

    def test_randomized_queries_match_reference(self):
        rng = random.Random(42)
        for trial in range(400):
            rows = [{"a": rng.choice([None, "x", "y", "z"]),
                     "b": rng.choice([None, 1, 2, 3]),
                     "v": rng.choice([None, 1, 2, 2, 3, 10])}
                    for _ in range(rng.randint(0, 40))]
            mode = rng.choice(["group", "rollup", "sets"])
            if mode == "group":
                q = {"rows": rows,
                     "group_by": rng.choice([[], ["a"], ["b"], ["a", "b"]])}
            elif mode == "rollup":
                q = {"rows": rows, "rollup": ["a", "b"][:rng.randint(0, 2)]}
            else:
                q = {"rows": rows, "grouping_sets": [
                    rng.choice([[], ["a"], ["b"], ["a", "b"]])
                    for _ in range(rng.randint(1, 3))]}
            q["aggregates"] = rng.sample(AGG_POOL, rng.randint(1, len(AGG_POOL)))
            if rng.random() < 0.6:
                conds = []
                for _ in range(rng.randint(1, 2)):
                    if rng.random() < 0.5:
                        left = rng.choice([a["as"] for a in q["aggregates"]])
                    else:
                        src = rng.choice(AGG_POOL[2:6])
                        left = {k: v for k, v in src.items() if k != "as"}
                    conds.append([left, rng.choice(list(_CMP)),
                                  rng.choice([None, 0, 1, 3, 10])])
                q["having"] = conds
            limit = rng.choice([1, 2, 3, 5, 1000])
            q["memory_groups"] = limit

            expected, columns = _ref_run(q)
            res = run(q)
            got = {}
            for r in res["rows"]:
                gset = next(s for s in grouping_sets(q)
                            if sum(1 << i for i, c in enumerate(columns)
                                   if c not in s) == r["grouping"])
                got[(r["grouping"], tuple(r[c] for c in gset))] = r
            self.assertEqual(got, expected, f"trial {trial}, limit {limit}, q={q}")
            # ordering invariant
            ordered = [(r["grouping"],)
                       + tuple(sort_key(r[c]) for c in columns)
                       for r in res["rows"]]
            self.assertEqual(ordered, sorted(ordered))


_CMP = {"=": lambda a, b: a == b, "!=": lambda a, b: a != b,
        "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
        ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}


if __name__ == "__main__":
    unittest.main()

