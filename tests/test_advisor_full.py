import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import advisor  # noqa: E402
import cost  # noqa: E402
import workload  # noqa: E402

STATS = {
    "t": {"rows": 1000,
          "columns": {"a": {"ndv": 100, "width": 4}, "b": {"ndv": 10, "width": 4},
                      "c": {"ndv": 5, "width": 4}},
          "indexes": []}}


def q(sql):
    return workload.parse(sql)


class ParsingTests(unittest.TestCase):
    def test_numeric_literals_are_fingerprinted(self):
        self.assertEqual(workload.fingerprint("SELECT * FROM t WHERE a = 1 AND b > 2.5;"),
                         workload.fingerprint("SELECT * FROM t WHERE a = 9 AND b > 7.0;"))
        self.assertNotEqual(workload.fingerprint("SELECT * FROM t WHERE a = 1;"),
                            workload.fingerprint("SELECT * FROM t WHERE b = 1;"))

    def test_multiple_statements_per_line_are_counted(self):
        import tempfile
        text = "# header\nSELECT * FROM t WHERE a = 1; SELECT * FROM t WHERE a = 2;\n"
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.write(text)
            path = f.name
        groups = workload.read_log(path)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["count"], 2)
        os.unlink(path)

    def test_predicate_and_order_parsing(self):
        parsed = q("SELECT * FROM t WHERE a = 5 AND b BETWEEN 1 AND 3 ORDER BY b DESC")
        self.assertEqual(parsed["preds"], [("t", "a", "eq"), ("t", "b", "range")])
        self.assertEqual(parsed["order"], ["b"])


class CostModelTests(unittest.TestCase):
    def test_seq_scan_and_index_access(self):
        self.assertEqual(cost.query_cost(STATS, q("SELECT * FROM t WHERE a = 5"), [])[0], 1000)
        ix = {"name": "ix_t_a", "table": "t", "columns": ["a"]}
        self.assertAlmostEqual(cost.query_cost(STATS, q("SELECT * FROM t WHERE a = 5"), [ix])[0],
                               3 + 1.5 * 10)

    def test_range_predicate_and_index_size(self):
        ix = {"name": "ix_t_a_b", "table": "t", "columns": ["a", "b"]}
        got = cost.query_cost(STATS, q("SELECT * FROM t WHERE a = 5 AND b > 2"), [ix])[0]
        self.assertAlmostEqual(got, 3 + 1.5 * 1000 / 100 * 0.1)
        self.assertEqual(cost.size(STATS, {"table": "t", "columns": ["a", "b"]}), 1000 * 16)

    def test_index_providing_order_skips_sort(self):
        ix = {"name": "ix_t_a_b", "table": "t", "columns": ["a", "b"]}
        with_order = cost.query_cost(STATS, q("SELECT * FROM t WHERE a = 5 ORDER BY b"), [ix])[0]
        no_index = cost.query_cost(STATS, q("SELECT * FROM t WHERE a = 5 ORDER BY b"), [])[0]
        self.assertAlmostEqual(with_order, 3 + 1.5 * 10)
        self.assertAlmostEqual(no_index, 1000 + 10)  # scan plus sort of 10 rows

    def test_insert_maintains_every_index(self):
        ixs = [{"name": "ix_t_a", "table": "t", "columns": ["a"]},
               {"name": "ix_t_b", "table": "t", "columns": ["b"]}]
        self.assertEqual(cost.query_cost(STATS, q("INSERT INTO t (a, b) VALUES (1, 2)"), ixs)[0],
                         2 * 2 * 1)  # WRITE * 1 row * 2 indexes

    def test_update_only_maintains_touched_indexes(self):
        ix_a = {"name": "ix_t_a", "table": "t", "columns": ["a"]}
        ix_b = {"name": "ix_t_b", "table": "t", "columns": ["b"]}
        val = cost.query_cost(STATS, q("UPDATE t SET a = 1 WHERE c = 2"), [ix_a, ix_b])[0]
        # locate via scan (1000, no matching index) + maintenance of ix_t_a only
        self.assertAlmostEqual(val, 1000 + 2 * (1000 / 5))

    def test_delete_maintains_every_index(self):
        ixs = [{"name": "ix_t_a", "table": "t", "columns": ["a"]},
               {"name": "ix_t_b", "table": "t", "columns": ["b"]}]
        val = cost.query_cost(STATS, q("DELETE FROM t WHERE c = 2"), ixs)[0]
        rows = 1000 / 5
        self.assertAlmostEqual(val, 1000 + 2 * rows * 2)


class CandidateTests(unittest.TestCase):
    def test_eq_range_and_eq_order_shapes(self):
        cols = {tuple(c["columns"]) for c in cost.candidates(
            STATS, q("SELECT * FROM t WHERE a = 1 AND b > 2 ORDER BY b"))}
        self.assertIn(("a", "b"), cols)       # E + [r]
        self.assertIn(("a",), cols)          # E

    def test_order_columns_already_in_equality_not_duplicated(self):
        cols = {tuple(c["columns"]) for c in cost.candidates(
            STATS, q("SELECT * FROM t WHERE a = 1 AND b = 2 ORDER BY a, b"))}
        self.assertNotIn(("a", "b", "a"), cols)
        self.assertNotIn(("a", "b", "b"), cols)

    def test_join_inner_column_candidate(self):
        stats = {
            "t": {"rows": 1000, "columns": {"a": {"ndv": 10, "width": 4},
                                            "uid": {"ndv": 20, "width": 4}}, "indexes": []},
            "u": {"rows": 500, "columns": {"id": {"ndv": 500, "width": 4}}, "indexes": []}}
        parsed = q("SELECT * FROM t JOIN u ON u.id = t.uid WHERE t.a = 1")
        names = [c["name"] for c in cost.candidates(stats, parsed)]
        self.assertIn("ix_u_id", names)


class AdvisorTests(unittest.TestCase):
    def setUp(self):
        self.stats = {
            "t": {"rows": 100000,
                  "columns": {"id": {"ndv": 100000, "width": 8}, "a": {"ndv": 100, "width": 4},
                              "b": {"ndv": 10, "width": 4}, "c": {"ndv": 5, "width": 4}},
                  "indexes": [
                      {"name": "pk_t", "columns": ["id"], "unique": True},
                      {"name": "ix_t_a", "columns": ["a"]},
                      {"name": "ix_t_a_b", "columns": ["a", "b"]},
                      {"name": "ix_t_c", "columns": ["c"]}]}}

    def advise(self, sql_counts, budget):
        queries = [{"fingerprint": workload.fingerprint(s), "sql": s, "count": n,
                    "parsed": workload.parse(s)} for s, n in sql_counts]
        return advisor.advise(self.stats, queries, budget)

    def test_redundant_uses_ordered_prefix_not_set_inclusion(self):
        report = self.advise([("SELECT * FROM t WHERE c = 1", 1)],
                             {"max_indexes": 1, "space_bytes": 10 ** 12})
        self.assertIn("ix_t_a", report["redundant"])          # prefix of (a, b)
        self.assertNotIn("ix_t_c", report["redundant"])       # (c) is NOT a prefix of (a, b)

    def test_unused_index_detected(self):
        report = self.advise([("SELECT * FROM t WHERE a = 1", 1)],
                             {"max_indexes": 1, "space_bytes": 10 ** 12})
        self.assertIn("ix_t_c", report["unused"])

    def test_write_amplification_can_reject_a_candidate(self):
        # one rare read vs. a flood of inserts: the new index must not pay back
        report = self.advise([
            ("SELECT * FROM t WHERE b = 1", 1),
            ("INSERT INTO t (id, a, b, c) VALUES (1, 2, 3, 4)", 100000)],
            {"max_indexes": 2, "space_bytes": 10 ** 12})
        self.assertEqual(report["recommended"], [])

    def test_greedy_picks_helpful_index_and_reports_benefit(self):
        report = self.advise([("SELECT * FROM t WHERE b = 1", 1000)],
                             {"max_indexes": 2, "space_bytes": 10 ** 12})
        self.assertEqual([c["name"] for c in report["recommended"]], ["ix_t_b"])
        entry = report["queries"][0]
        self.assertGreater(entry["benefit"], 0)
        self.assertLess(entry["cost_after"], entry["cost_before"])

    def test_space_budget_limits_selection(self):
        tiny = {"max_indexes": 5, "space_bytes": 1}  # nothing fits
        report = self.advise([("SELECT * FROM t WHERE b = 1", 1000)], tiny)
        self.assertEqual(report["recommended"], [])

    def test_count_budget_limits_selection(self):
        report = self.advise([
            ("SELECT * FROM t WHERE b = 1", 100),
            ("SELECT * FROM t WHERE c = 3", 100)],
            {"max_indexes": 1, "space_bytes": 10 ** 12})
        self.assertEqual(len(report["recommended"]), 1)


if __name__ == "__main__":
    unittest.main()
