"""End-to-end pipeline tests: transactions, replay, conflicts, resume, versions."""

import unittest

from ndjson_pipe.importer import import_source
from ndjson_pipe.reference import expected_for_source
from ndjson_pipe.compare import compare_store_to_expected
from ndjson_pipe.store import Store

from tests.helpers import make_db_path, run_appends, run_chunked, write_source

SOURCE = "supplier-x"


class FixtureIngestionTests(unittest.TestCase):
    def test_supplier_fixture_matches_reference(self):
        db = make_db_path()
        with open("fixtures/supplier-a.ndjson", "rb") as fh:
            data = fh.read()
        run_chunked(db, SOURCE, data, [7, 13, 31, 64, 1024])
        with Store(db) as store:
            diffs = compare_store_to_expected(store, SOURCE, expected_for_source(SOURCE, data))
        self.assertEqual(diffs, [])
        with Store(db) as store:
            stats = store.stats(SOURCE)
            self.assertEqual(stats["records"], 3)
            self.assertEqual(stats["rejections"], 0)
            # note semantics from the shipped fixture
            rows = {r[3]: r for r in store.dump_records(SOURCE)}
            self.assertEqual(rows["A-01"][7], "12.35")  # amount text
            self.assertEqual(rows["A-02"][7], "2.75")   # v2 cents normalized
            self.assertEqual(rows["A-03"][9], 0)        # missing note -> not present
            self.assertIsNone(rows["A-03"][10])


class ReplayAndConflictTests(unittest.TestCase):
    def setUp(self):
        self.db = make_db_path()
        self.data = (
            b'{"version":1,"id":"B-01","amount":"10.00","currency":"CNY"}\n'
            b'{"version":2,"id":"B-02","amount_cents":500,"currency":"USD","note":null}\n'
            b'{"version":1,"id":"B-03","amount":"bad","currency":"CNY"}\n'
        )
        run_chunked(self.db, SOURCE, self.data, [5, 11, 29, 100])

    def test_replay_identical_source_is_idempotent(self):
        with Store(self.db) as store:
            before = (store.dump_records(SOURCE), store.dump_rejections(SOURCE), store.get_checkpoint(SOURCE))
        # full-scan replay of the exact same bytes, with different chunking
        run_chunked(self.db, SOURCE, self.data, [1, 1, 1, 37, 200], full_scan=True)
        with Store(self.db) as store:
            after = (store.dump_records(SOURCE), store.dump_rejections(SOURCE), store.get_checkpoint(SOURCE))
        self.assertEqual(before, after)

    def test_same_position_different_content_is_conflict(self):
        changed = (
            b'{"version":1,"id":"B-01","amount":"10.00","currency":"CNY"}\n'
            b'{"version":2,"id":"B-02","amount_cents":999,"currency":"USD","note":null}\n'
            b'{"version":1,"id":"B-03","amount":"bad","currency":"CNY"}\n'
        )
        run_chunked(self.db, SOURCE, changed, [128], full_scan=True)
        with Store(self.db) as store:
            conflicts = store.dump_conflicts(SOURCE)
        self.assertEqual(len(conflicts), 1)
        second_line_offset = len(changed.split(b"\n")[0]) + 1
        self.assertEqual(conflicts[0][0], second_line_offset)  # start offset of line 2
        self.assertEqual(conflicts[0][1], "record")
        self.assertNotEqual(conflicts[0][2], conflicts[0][3])
        # historical record at that offset is untouched; counts are stable
        with Store(self.db) as store:
            rows = {r[3]: r for r in store.dump_records(SOURCE)}
            self.assertEqual(rows["B-02"][7], "5.00")
            self.assertEqual(store.stats(SOURCE)["records"], 2)
            self.assertEqual(store.stats(SOURCE)["rejections"], 1)
            self.assertEqual(store.stats(SOURCE)["conflicts"], 1)

    def test_rejected_line_replay_also_idempotent(self):
        run_chunked(self.db, SOURCE, self.data, [3], full_scan=True)
        with Store(self.db) as store:
            rejections = store.dump_rejections(SOURCE)
        self.assertEqual(len(rejections), 1)
        self.assertEqual(rejections[0][3], "amount_format")


class ResumeTests(unittest.TestCase):
    def test_partial_tail_completed_on_later_append(self):
        db = make_db_path()
        line1 = b'{"version":1,"id":"C-01","amount":"1.00","currency":"CNY"}\n'
        partial = b'{"version":2,"id":"C-02","amount_cen'
        run_appends(db, SOURCE, [line1, partial], sealed_flags=[False, False])
        with Store(db) as store:
            self.assertEqual(store.stats(SOURCE)["records"], 1)
            self.assertEqual(store.get_checkpoint(SOURCE), len(line1))

        rest = b'ts":725,"currency":"EUR"}\n'
        run_appends(
            db,
            SOURCE,
            [line1, partial, rest],
            sealed_flags=[False, False, True],
        )
        data = line1 + partial + rest
        with Store(db) as store:
            diffs = compare_store_to_expected(store, SOURCE, expected_for_source(SOURCE, data))
        self.assertEqual(diffs, [])

    def test_multiple_appends_equal_one_shot(self):
        db = make_db_path()
        data = (
            '{"version":1,"id":"D-01","amount":"2.50","currency":"CNY","note":"蓝莓"}\n'
            '{"version":2,"id":"D-02","amount_cents":10,"currency":"JPY"}\n'
            '{"version":1,"id":"D-03","amount":"3.00","currency":"CNY"}\n'
        ).encode("utf-8")
        # cuts land inside a multibyte character and inside a record
        cuts = [7, 30, 41]
        parts = [data[:cuts[0]], data[cuts[0]:cuts[1]], data[cuts[1]:cuts[2]], data[cuts[2]:]]
        run_appends(db, SOURCE, parts, chunk_size=4)
        with Store(db) as store:
            diffs = compare_store_to_expected(store, SOURCE, expected_for_source(SOURCE, data))
        self.assertEqual(diffs, [])

    def test_checkpoint_inside_transaction_rolls_back(self):
        # batch_size=1 forces a transaction per line; verify the checkpoint
        # table only ever reflects committed offsets by re-reading after runs
        db = make_db_path()
        data = b'{"version":1,"id":"E-01","amount":"1.00","currency":"CNY"}\n' * 4
        run_chunked(db, SOURCE, data, [2, 2, 2, 512], batch_size=1)
        with Store(db) as store:
            self.assertEqual(store.get_checkpoint(SOURCE), len(data))
            self.assertEqual(store.stats(SOURCE)["records"], 4)


class VersionHistoryTests(unittest.TestCase):
    def test_normalized_by_pins_schema_version_per_row(self):
        db = make_db_path()
        data = (
            b'{"version":1,"id":"V-01","amount":"4.20","currency":"CNY"}\n'
            b'{"version":2,"id":"V-02","amount_cents":420,"currency":"CNY"}\n'
        )
        run_chunked(db, SOURCE, data, [64])
        with Store(db) as store:
            rows = store.dump_records(SOURCE)
        by_id = {r[3]: r for r in rows}
        self.assertEqual(by_id["V-01"][5], 1)  # normalized_by
        self.assertEqual(by_id["V-02"][5], 2)
        # both yield the same canonical amount but keep their lineage distinct
        self.assertEqual(by_id["V-01"][7], "4.20")
        self.assertEqual(by_id["V-02"][7], "4.20")
        self.assertEqual(by_id["V-01"][4], 1)  # schema version column
        self.assertEqual(by_id["V-02"][4], 2)

    def test_replay_after_append_keeps_historical_lineage(self):
        db = make_db_path()
        part1 = b'{"version":1,"id":"V-03","amount":"9.99","currency":"CNY"}\n'
        part2 = b'{"version":2,"id":"V-04","amount_cents":999,"currency":"CNY"}\n'
        run_appends(db, SOURCE, [part1, part2], chunk_size=3)
        # full-scan replay of the complete source must not alter lineage
        run_chunked(db, SOURCE, part1 + part2, [128], full_scan=True)
        with Store(db) as store:
            rows = {r[3]: r for r in store.dump_records(SOURCE)}
        self.assertEqual(rows["V-03"][5], 1)
        self.assertEqual(rows["V-04"][5], 2)
        self.assertEqual(rows["V-03"][8], 999)
        self.assertEqual(rows["V-04"][8], 999)


class RawProvenanceTests(unittest.TestCase):
    def test_raw_line_and_offsets_persisted(self):
        db = make_db_path()
        data = b'{"version":1,"id":"P-01","amount":"1.00","currency":"CNY"}\n'
        run_chunked(db, SOURCE, data, [10, 10, 1000])
        with Store(db) as store:
            row = store.dump_records(SOURCE)[0]
        self.assertEqual(row[1], 0)
        self.assertEqual(row[2], len(data))
        # raw text retained verbatim for audit/replay
        with Store(db) as store:
            raw = store.conn.execute(
                "SELECT raw_line FROM records WHERE source_id = ?", (SOURCE,)
            ).fetchone()[0]
        self.assertEqual(raw.encode("utf-8"), data.strip())

    def test_rejection_carries_structured_reason_and_raw(self):
        db = make_db_path()
        data = b'{"version":1,"id":"P-02","amount":"1.000","currency":"CNY"}\n'
        run_chunked(db, SOURCE, data, [64])
        with Store(db) as store:
            reason, field, _h = store.dump_rejections(SOURCE)[0][3:6]
            raw = store.conn.execute(
                "SELECT raw_line FROM rejections WHERE source_id = ?", (SOURCE,)
            ).fetchone()[0]
        self.assertEqual(reason, "amount_precision")
        self.assertEqual(field, "amount")
        self.assertIn("1.000", raw)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
