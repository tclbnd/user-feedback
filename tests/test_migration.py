import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dialects  # noqa: E402
import migrate  # noqa: E402
from sqlparse import Parser  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = json.load(open(os.path.join(ROOT, "examples", "shop.json"), encoding="utf-8"))
QUERIES = json.load(open(os.path.join(ROOT, "examples", "queries.json"), encoding="utf-8"))
SRC = {q["name"]: q["source"] for q in QUERIES}


def page(dialect, sql, rows, size, offset, pk="id"):
    q = Parser(sql).parse()
    q["limit"], q["offset"] = size, offset
    return dialects.execute(dialect, q, rows, primary_key=pk)


class RewriteRuleTests(unittest.TestCase):
    def test_limit_offset_count_and_plain_limit(self):
        rw = migrate.rewrite("SELECT id FROM orders ORDER BY id LIMIT 3, 5", DATA)
        self.assertIn("LIMIT 5 OFFSET 3", rw)
        rw = migrate.rewrite("SELECT id FROM orders ORDER BY id LIMIT 5", DATA)
        self.assertIn("LIMIT 5 OFFSET 0", rw)

    def test_null_position_follows_legacy_lowest_null(self):
        rw = migrate.rewrite("SELECT id FROM orders ORDER BY city", DATA)
        self.assertIn("LOWER(city) ASC NULLS FIRST", rw)
        rw = migrate.rewrite("SELECT id FROM orders ORDER BY city DESC", DATA)
        self.assertIn("LOWER(city) DESC NULLS LAST", rw)
        # an explicit NULLS clause is preserved
        rw = migrate.rewrite("SELECT id FROM orders ORDER BY city ASC NULLS LAST", DATA)
        self.assertIn("NULLS LAST", rw)

    def test_text_columns_folded_in_where_and_order(self):
        rw = migrate.rewrite(
            "SELECT id FROM orders WHERE customer = 'ALICE' ORDER BY customer", DATA)
        self.assertIn("LOWER(customer) = 'alice'", rw)
        self.assertIn("ORDER BY LOWER(customer) ASC", rw)

    def test_date_functions_rewritten_with_zone(self):
        rw = migrate.rewrite(
            "SELECT id, DATE(created_at) AS d, CONVERT_TZ(created_at, '+08:00', '+00:00') AS u "
            "FROM orders ORDER BY id", DATA)
        self.assertIn("DATE_TRUNC('day', created_at, '+08:00') AS d", rw)
        self.assertIn("TIMEZONE('+00:00', created_at) AS u", rw)
        # bare timestamp selection is printed as local session-zone text on both sides
        rw = migrate.rewrite("SELECT id, created_at FROM orders ORDER BY id", DATA)
        self.assertIn("TIMEZONE('+08:00', created_at)", rw)
        with self.assertRaises(SystemExit):
            migrate.rewrite(
                "SELECT id FROM orders WHERE id = 1 ORDER BY CONVERT_TZ(created_at, '+00:00', '+08:00')",
                DATA)

    def test_primary_key_appended_once_as_tiebreaker(self):
        rw = migrate.rewrite("SELECT id, amount FROM orders ORDER BY amount DESC", DATA)
        self.assertTrue(rw.endswith("id ASC NULLS FIRST"))
        rw = migrate.rewrite("SELECT id, amount FROM orders ORDER BY id, amount", DATA)
        self.assertEqual(rw.count("id ASC NULLS FIRST"), 1)
        self.assertNotIn(", id ASC NULLS FIRST,", rw)


class RowByRowEquivalenceTests(unittest.TestCase):
    def assert_legacy_target_match(self, name):
        target_sql = migrate.rewrite(SRC[name], DATA)
        table = Parser(SRC[name]).parse()["table"]
        want = dialects.execute("legacy", migrate.stabilized(SRC[name], DATA), DATA["legacy"][table])
        got = dialects.execute("target", target_sql, DATA["target"][table])
        self.assertEqual(got, want, f"{name}:\n want={want}\n got ={got}")

    def test_all_rewritten_queries_match_row_by_row(self):
        for name in ("page1_by_amount", "page2_by_amount", "city_nulls",
                     "customers_ci", "order_day"):
            self.assert_legacy_target_match(name)

    def test_paging_is_stable_no_repeat_or_skip(self):
        base = "SELECT id, amount FROM orders WHERE amount = 30 ORDER BY amount DESC"
        rewritten = migrate.rewrite(base + " LIMIT 0, 2", DATA)
        stabilized = migrate.stabilized(base, DATA)          # PK appended, as in `check`
        legacy_ids, target_ids, naive_ids = [], [], []
        for off in range(0, 8, 2):
            lq = Parser(base).parse()
            lq["order"] = [tuple(k) for k in stabilized["order"]]
            lq["limit"], lq["offset"] = 2, off
            legacy_ids += [r["id"] for r in dialects.execute(
                "legacy", lq, DATA["legacy"]["orders"])]
            target_ids += [r["id"] for r in page(
                "target", rewritten, DATA["target"]["orders"], 2, off)]
            naive_ids += [r["id"] for r in page(
                "target",
                "SELECT id, amount FROM orders WHERE amount = 30 ORDER BY amount DESC",
                DATA["target"]["orders"], 2, off, pk=None)]
        # four amount=30 rows: 7, 5, 9, 3; with the PK tiebreaker every tie is
        # broken by ascending id, so both sides page through 3, 5, 7, 9.
        self.assertEqual(sorted(legacy_ids), [3, 5, 7, 9])
        self.assertEqual(target_ids, legacy_ids)          # rewritten: identical page order
        self.assertEqual(len(set(target_ids)), 4)         # no repeated rows
        # without the tiebreaker the target top-N sort moves ties between pages:
        # rows are repeated or skipped across the same LIMIT/OFFSET windowing.
        self.assertNotEqual(naive_ids, legacy_ids)
        self.assertTrue(len(naive_ids) != len(set(naive_ids))
                        or set(naive_ids) != set(legacy_ids))

    def test_cross_zone_day_truncation(self):
        # 16:30 UTC is 00:30 the next day in +08:00: the local day crosses the
        # boundary while the UTC day stays on 2026-03-01.
        rows = [{"id": 1, "created_at": "2026-03-01T16:30:00Z"}]
        out = dialects.execute(
            "target",
            "SELECT DATE_TRUNC('day', created_at, '+08:00') AS local_day, "
            "DATE_TRUNC('day', created_at) AS utc_day, "
            "TIMEZONE('+08:00', created_at) AS local_ts FROM x", rows)
        self.assertEqual(out, [{"local_day": "2026-03-02", "utc_day": "2026-03-01",
                                "local_ts": "2026-03-02 00:30:00"}])
        # the hand-written two-arg DATE_TRUNC(ts) keeps UTC-day semantics
        out = dialects.execute(
            "target", "SELECT DATE_TRUNC(created_at) AS d FROM x", rows)
        self.assertEqual(out, [{"d": "2026-03-01"}])


class DiagnosisTests(unittest.TestCase):
    def test_report_classifies_data_and_rewrite_causes(self):
        report = {e["name"]: e for e in migrate.check(DATA, QUERIES)}
        for name in ("page1_by_amount", "page2_by_amount", "city_nulls",
                     "customers_ci", "order_day"):
            self.assertEqual(report[name]["status"], "match", name)
            self.assertIsNone(report[name]["cause"])
        # target copy changed id=4's amount 20 -> 25: rewrite is fine on mirrored data
        self.assertEqual(report["amounts"]["status"], "mismatch")
        self.assertEqual(report["amounts"]["cause"], "data")
        self.assertEqual(report["amounts"]["first_diff"], 3)
        # hand-written target SQL forgets the +08:00 zone: even on mirrored data it fails
        self.assertEqual(report["hand_written_day"]["status"], "mismatch")
        self.assertEqual(report["hand_written_day"]["cause"], "rewrite")

    def test_mirror_converts_legacy_timestamps_to_utc(self):
        mirror = migrate.mirror_legacy(DATA, "orders")
        by_id = {r["id"]: r for r in mirror}
        self.assertEqual(by_id[7]["created_at"], "2026-03-01T15:30:00Z")
        self.assertEqual(by_id[1]["created_at"], "2026-02-28T22:45:00Z")
        # non-timestamp columns are carried through untouched
        self.assertEqual(by_id[7]["customer"], "bob")
        self.assertIsNone(by_id[2]["city"])


if __name__ == "__main__":
    unittest.main()
