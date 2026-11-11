"""Differential regression against the independent batch reference.

Coverage-feedback loop: legal synthetic corpus structures and chunk-split
combinations are expanded first (randomized, seeded), while business
rejections are fixed samples. Every run must equal the batch reference under
different chunk sizes, append orders and replay counts.
"""

import random
import unittest

from ndjson_pipe.compare import compare_store_to_expected
from ndjson_pipe.reference import (
    expected_for_append_sequence,
    expected_for_deliveries,
    expected_for_source,
)
from ndjson_pipe.store import Store

from tests.corpus import (
    chunk_byte_sizes,
    expected_reject_reasons,
    fixed_reject_ndjson,
    legal_ndjson,
    split_at_offsets,
)
from tests.helpers import make_db_path, run_appends, run_chunked

SOURCE = "supplier-diff"


class DifferentialMatrixTests(unittest.TestCase):
    def _assert_matches_reference(self, db, data):
        with Store(db) as store:
            diffs = compare_store_to_expected(store, SOURCE, expected_for_source(SOURCE, data))
        self.assertEqual(diffs, [], msg="\n".join(diffs))

    def test_legal_corpus_many_chunk_sizes(self):
        for seed in range(6):
            with self.subTest(seed=seed):
                data = legal_ndjson(seed=100 + seed, n=40)
                sizes = chunk_byte_sizes(seed=200 + seed, data_len=len(data))
                db = make_db_path()
                run_chunked(db, SOURCE, data, sizes, batch_size=7)
                self._assert_matches_reference(db, data)

    def test_legal_corpus_append_orders(self):
        data = legal_ndjson(seed=7, n=35)
        rng = random.Random(11)
        for trial in range(5):
            with self.subTest(trial=trial):
                # random cuts, including cuts inside multi-byte chars and lines
                cuts = sorted(rng.sample(range(1, len(data)), rng.randint(2, 8)))
                parts = split_at_offsets(data, cuts)
                db = make_db_path()
                run_appends(db, SOURCE, parts, chunk_size=rng.choice((1, 3, 9, 33, 128)))
                with Store(db) as store:
                    expected = expected_for_append_sequence(SOURCE, parts)
                    diffs = compare_store_to_expected(store, SOURCE, expected)
                self.assertEqual(diffs, [])

    def test_replay_counts_converge(self):
        data = legal_ndjson(seed=3, n=25)
        for replays in (1, 2, 4):
            with self.subTest(replays=replays):
                db = make_db_path()
                run_chunked(db, SOURCE, data, [16])
                deliveries = [data]
                for r in range(replays):
                    sizes = chunk_byte_sizes(50 + r, len(data))
                    run_chunked(db, SOURCE, data, sizes, full_scan=True, batch_size=11)
                    deliveries.append(data)
                with Store(db) as store:
                    diffs = compare_store_to_expected(
                        store, SOURCE, expected_for_deliveries(SOURCE, deliveries)
                    )
                self.assertEqual(diffs, [], msg="\n".join(diffs))
                with Store(db) as store:
                    stats = store.stats(SOURCE)
                self.assertEqual(stats["conflicts"], 0)
                # identical redeliveries never add rows
                self._assert_matches_reference(db, data)

    def test_fixed_reject_corpus_classification(self):
        data = fixed_reject_ndjson()
        db = make_db_path()
        run_chunked(db, SOURCE, data, chunk_byte_sizes(9, len(data)), batch_size=3)
        with Store(db) as store:
            diffs = compare_store_to_expected(store, SOURCE, expected_for_source(SOURCE, data))
        self.assertEqual(diffs, [])
        with Store(db) as store:
            actual_reasons = sorted(r[3] for r in store.dump_rejections(SOURCE))
        self.assertEqual(actual_reasons, sorted(expected_reject_reasons()))

    def test_mixed_legal_and_fixed_rejects(self):
        legal = legal_ndjson(seed=21, n=20)
        data = legal + fixed_reject_ndjson() + legal_ndjson(seed=22, n=10)
        db = make_db_path()
        # chunk size of 1 byte maximally stresses cross-chunk reassembly
        run_chunked(db, SOURCE, data, [1] * len(data), batch_size=5)
        self._assert_matches_reference(db, data)

    def test_blank_lines_and_crlf_against_reference(self):
        base = legal_ndjson(seed=31, n=15).replace(b"\n", b"\r\n")
        data = b"\r\n" + base + b"\r\n"
        db = make_db_path()
        run_chunked(db, SOURCE, data, chunk_byte_sizes(4, len(data)))
        self._assert_matches_reference(db, data)

    def test_repeated_redelivery_with_changed_content(self):
        # First delivery: canonical shards. Second delivery: same logical
        # source positions, one line changed. The reference models the exact
        # delivery sequence and the pipeline must match it row for row.
        lines = legal_ndjson(seed=13, n=12).splitlines(keepends=True)
        changed = list(lines)
        changed[4] = b'{"version":2,"id":"MUTATED","amount_cents":1,"currency":"USD"}\n'
        delivery_1, delivery_2 = b"".join(lines), b"".join(changed)

        expected = expected_for_deliveries(SOURCE, [delivery_1, delivery_2])

        db = make_db_path()
        # Two physical files at the same logical source: full-scan replays.
        run_chunked(db, SOURCE, delivery_1, [37], full_scan=True)
        run_chunked(db, SOURCE, delivery_2, [37], full_scan=True)
        with Store(db) as store:
            diffs = compare_store_to_expected(store, SOURCE, expected)
        self.assertEqual(diffs, [], msg="\n".join(diffs))
        with Store(db) as store:
            self.assertEqual(store.stats(SOURCE)["conflicts"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
