import os
import tempfile
import unittest

from pipeline import importer
from pipeline.store import Store

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "fixtures", "supplier-a.ndjson")


class ImporterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ndjson-test-")
        self.db = os.path.join(self.tmp.name, "t.db")
        self.src = os.path.join(self.tmp.name, "src.ndjson")

    def tearDown(self):
        self.tmp.cleanup()

    def _store(self):
        return Store(self.db)

    def _write(self, data):
        with open(self.src, "wb") as fh:
            fh.write(data)

    def test_fixture_ingest_and_idempotent_replay(self):
        store = self._store()
        stats = importer.ingest(store, "A", FIXTURE, chunk_size=7, seal=True)
        self.assertEqual(stats.records, 3)
        self.assertEqual(store.get_checkpoint("A"),
                         (os.path.getsize(FIXTURE), 1))
        dump1 = store.dump("A")
        # replaying a sealed source is a no-op
        stats = importer.ingest(store, "A", FIXTURE, chunk_size=7, seal=True)
        self.assertEqual(stats.records + stats.rejects, 0)
        self.assertEqual(store.dump("A"), dump1)
        store.close()

    def test_appends_and_partial_tail(self):
        with open(FIXTURE, "rb") as fh:
            data = fh.read()
        cut = len(data) // 2  # split mid-line
        self._write(data[:cut])
        store = self._store()
        importer.ingest(store, "A", self.src, chunk_size=5)
        partial_dump = store.dump("A")
        self.assertIn("K|", partial_dump)
        with open(self.src, "ab") as fh:
            fh.write(data[cut:])
        importer.ingest(store, "A", self.src, chunk_size=5, seal=True)
        self.assertEqual(store.count("records", "A"), 3)
        store.close()

    def test_replay_after_rewind_skips_identical_content(self):
        self._write(b'{"version":2,"id":"X","amount_cents":1,'
                    b'"currency":"CNY"}\n' * 4)
        store = self._store()
        importer.ingest(store, "S", self.src, chunk_size=16, seal=True)
        dump1 = store.dump("S")
        store.reset_checkpoint("S", 0)
        stats = importer.ingest(store, "S", self.src, chunk_size=16,
                                seal=True)
        self.assertEqual(stats.skips, 4)
        self.assertEqual(stats.conflicts, 0)
        self.assertEqual(store.dump("S"), dump1)
        store.close()

    def test_conflict_on_different_content_at_same_offset(self):
        line1 = b'{"version":2,"id":"X","amount_cents":100,"currency":"CNY"}\n'
        line2 = b'{"version":2,"id":"X","amount_cents":200,"currency":"CNY"}\n'
        assert len(line1) == len(line2)
        self._write(line1)
        store = self._store()
        importer.ingest(store, "S", self.src, seal=True)
        self._write(line2)
        store.reset_checkpoint("S", 0)
        stats = importer.ingest(store, "S", self.src, seal=True)
        self.assertEqual(stats.conflicts, 1)
        self.assertEqual(store.count("conflicts", "S"), 1)
        # original record is untouched
        rows = list(store.iter_records("S"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][3], "1.00")
        store.close()

    def test_norm_version_preserved_on_rewind_replay(self):
        # norm v1 rejects a missing currency; norm v2 defaults it to CNY
        line = b'{"version":1,"id":"V","amount":"3.50"}\n'
        self._write(line)
        os.environ["NDJSON_NORM_VERSION"] = "1"
        try:
            store = self._store()
            importer.ingest(store, "S", self.src, seal=True)
        finally:
            del os.environ["NDJSON_NORM_VERSION"]
        self.assertEqual(store.count("rejects", "S"), 1)
        dump1 = store.dump("S")
        # restore old checkpoint, replay under the NEW normalization version
        os.environ["NDJSON_NORM_VERSION"] = "2"
        try:
            store.reset_checkpoint("S", 0)
            importer.ingest(store, "S", self.src, seal=True)
        finally:
            del os.environ["NDJSON_NORM_VERSION"]
        # historical row keeps its original meaning (still a reject)
        self.assertEqual(store.dump("S"), dump1)
        self.assertEqual(store.count("records", "S"), 0)
        store.close()


if __name__ == "__main__":
    unittest.main()
