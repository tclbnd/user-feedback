"""Acceptance tests for the ID-based legacy Parquet importer.

Run with ``python3 -m unittest discover -s . -p 'test_*.py'``.
All fixtures are synthetic.
"""

from __future__ import annotations

import json
import random
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

import import_history as subject


def f(name, kind, field_id=None):
    metadata = None
    if field_id is not None:
        metadata = {b"PARQUET:field_id": str(field_id).encode()}
    return pa.field(name, kind, metadata=metadata)


DEC = pa.decimal128(14, 3)
DEC20 = pa.decimal128(20, 3)


def generation_schemas():
    """Four schema generations sharing logical IDs 1..7 across renames."""

    return {
        "gen1": [
            f("customer", pa.string(), 1),
            f("label", pa.string(), 2),
            f("rank", DEC, 3),
        ],
        # gen2 renames 1 and 2; a *new* column reuses the old name "label" with
        # a fresh ID (90) and must never be joined to title.
        "gen2": [
            f("customer", pa.string(), 1),
            f("label", pa.string(), 90),
            f("renamed", pa.string(), 2),
            f("charge", DEC, 3),
            f(
                "entries",
                pa.list_(
                    pa.struct([f("what", pa.string(), 5), f("price", DEC, 6)])
                ),
                4,
            ),
            f("at", pa.timestamp("ms", tz="UTC"), 7),
        ],
        # gen3 renames the nested children (5, 6) and the top-level id.
        "gen3": [
            f("id", pa.string(), 1),
            f("label", pa.string(), 90),
            f(
                "entries",
                pa.list_(
                    pa.struct([f("name", pa.string(), 5), f("total", DEC, 6)])
                ),
                4,
            ),
            f("at", pa.timestamp("ms", tz="UTC"), 7),
        ],
        # gen4 is a minimal projection: no title, score or items.
        "gen4": [
            f("id", pa.string(), 1),
            f("at", pa.timestamp("us", tz="UTC"), 7),
        ],
    }


def generation_rows():
    return {
        "gen1": [
            {"customer": "a1", "label": "first", "rank": Decimal("7.255")},
            {"customer": "a2", "label": None, "rank": None},
        ],
        "gen2": [
            {
                "customer": "b1",
                "label": "decoy-label",
                "renamed": "kept",
                "charge": Decimal("7.245"),
                "entries": [
                    {"what": "usd", "price": Decimal("1.255")},
                    None,
                ],
                "at": datetime(2026, 1, 1, 0, 0, 0, 123456, tzinfo=timezone.utc),
            },
            {
                "customer": "b2",
                "label": "decoy-label",
                "renamed": None,
                "charge": Decimal("999999.000"),
                "entries": [],
                "at": None,
            },
        ],
        "gen3": [
            {
                "id": "c1",
                "label": "decoy-label",
                "entries": [],
                "at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            },
            {
                "id": "c2",
                "label": "decoy-label",
                "entries": None,
                "at": None,
            },
        ],
        "gen4": [
            {
                "id": "d1",
                "at": datetime(2026, 1, 1, 0, 0, 0, 500000, tzinfo=timezone.utc),
            },
            {"id": "d2", "at": None},
        ],
    }


# Written out by hand from the target contract, independently of the importer.
EXPECTED_ROWS = [
    {"id": "a1", "title": "first", "score": "7.26", "items": None, "utc": None},
    {"id": "a2", "title": None, "score": None, "items": None, "utc": None},
    {
        "id": "b1",
        "title": "kept",
        "score": "7.24",
        "items": [{"name": "usd", "amount": "1.26"}, None],
        # Source unit is milliseconds, so the 123456 microseconds of the
        # Python datetime are truncated to 123 ms on write.
        "utc": "2026-01-01T00:00:00.123Z",
    },
    {
        "id": "c1",
        "title": "untitled",
        "score": None,
        "items": [],
        "utc": "2026-01-01T00:00:00Z",
    },
    {
        "id": "c2",
        "title": "untitled",
        "score": None,
        "items": None,
        "utc": None,
    },
    {
        "id": "d1",
        "title": "untitled",
        "score": None,
        "items": None,
        "utc": "2026-01-01T00:00:00.5Z",
    },
    {"id": "d2", "title": "untitled", "score": None, "items": None, "utc": None},
]

EXPECTED_OVERFLOW_REJECT = {
    "file": "gen2.parquet",
    "row": 2,
    "field": "score",
    "reason": "DECIMAL_OVERFLOW",
    "source": "charge",
    "detail": "999999.000 rounds to 999999.00 outside [-9999.99, 9999.99]",
}


class Fixture:
    """Synthetic input directory: four generations plus legacy cases."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        schemas = generation_schemas()
        rows = generation_rows()
        for name in sorted(schemas):
            pq.write_table(
                pa.Table.from_pylist(rows[name], schema=pa.schema(schemas[name])),
                self.root / (name + ".parquet"),
            )

        # Legacy file without IDs, resolved by an explicit sidecar.
        legacy_schema = pa.schema(
            [
                pa.field("customer", pa.string()),
                pa.field("title", pa.string()),
                pa.field(
                    "entries",
                    pa.list_(
                        pa.struct(
                            [
                                pa.field("nm", pa.string()),
                                pa.field("amt", DEC),
                            ]
                        )
                    ),
                ),
                pa.field("when", pa.timestamp("us", tz="UTC")),
            ]
        )
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "customer": "e1",
                        "title": "mapped",
                        "entries": [{"nm": "eur", "amt": Decimal("2.675")}],
                        "when": datetime(
                            2026, 1, 1, 12, 34, 56, 789000, tzinfo=timezone.utc
                        ),
                    }
                ],
                schema=legacy_schema,
            ),
            self.root / "legacy.parquet",
        )
        (self.root / "legacy.ids.json").write_text(
            json.dumps(
                {
                    "customer": 1,
                    "title": 2,
                    "entries": 4,
                    "entries.nm": 5,
                    "entries.amt": 6,
                    "when": 7,
                }
            ),
            encoding="utf-8",
        )

        # Truly ambiguous legacy file: no IDs, no sidecar, reused name.
        pq.write_table(
            pa.Table.from_pylist(
                [{"id": "f1", "same": "?"}],
                schema=pa.schema(
                    [pa.field("id", pa.string()), pa.field("same", pa.string())]
                ),
            ),
            self.root / "ambiguous.parquet",
        )

        # Naive timestamp: UTC semantics cannot be established.
        pq.write_table(
            pa.Table.from_pylist(
                [{"id": "g1", "at": datetime(2026, 1, 1)}],
                schema=pa.schema(
                    [f("id", pa.string(), 1), f("at", pa.timestamp("us"), 7)]
                ),
            ),
            self.root / "naive.parquet",
        )


def run_import(root: Path):
    return subject.import_directory(root)


def write_partitioned_generations(root: Path, split_seed=7):
    """Write the same four generations as several partitions.

    Each generation's rows are split across two files with different column
    *order* and different physical file names, which is exactly the situation
    where name-based matching mixed generations up.  The merged result must be
    identical to the single-file-per-generation fixture.
    """

    rng = random.Random(split_seed)
    schemas = generation_schemas()
    rows = generation_rows()
    written = []
    for generation in sorted(schemas):
        schema_fields = list(schemas[generation])
        generation_rows_list = list(rows[generation])
        half = len(generation_rows_list) // 2 or 1
        chunks = [generation_rows_list[:half], generation_rows_list[half:]]
        for part, chunk in enumerate(chunks):
            if not chunk:
                continue
            shuffled = list(schema_fields)
            rng.shuffle(shuffled)
            name = "{}-part{}.parquet".format(generation, part)
            pq.write_table(
                pa.Table.from_pylist(chunk, schema=pa.schema(shuffled)),
                root / name,
            )
            written.append(name)
    return written


class CrossPartitionTests(unittest.TestCase):
    """Four generations split into many partitions, imported in one pass."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="imphist-parts-"))
        self.root = self.tmp / "parts"
        self.root.mkdir()
        self.written = write_partitioned_generations(self.root)
        # The legacy/sidecar and diagnostic cases from the single-file fixture.
        Fixture(self.tmp / "reference")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_partitioned_import_equals_reference(self):
        reference = run_import(self.tmp / "reference")
        partitioned = run_import(self.root)
        # Every generation row must be identical to the single-file run.
        reference_generation = [
            row for row in reference["rows"] if row["id"][0] in "abcd"
        ]
        self.assertEqual(partitioned["rows"], reference_generation)
        # The only overflow row (b2) is isolated wherever its partition sits.
        overflow = [
            r for r in partitioned["rejects"] if r["reason"] == "DECIMAL_OVERFLOW"
        ]
        self.assertEqual(len(overflow), 1)
        self.assertEqual(overflow[0]["field"], "score")
        self.assertEqual(overflow[0]["source"], "charge")
        self.assertEqual(overflow[0]["detail"], EXPECTED_OVERFLOW_REJECT["detail"])
        self.assertNotIn("b2", [row["id"] for row in partitioned["rows"]])

    def test_partition_schema_versions_are_four(self):
        result = run_import(self.root)
        versions = result["manifest"]["schema_versions"]
        # Each generation has a different field order per part, so both parts
        # of a generation share a normalized digest.
        self.assertEqual(len(versions), 4)
        self.assertEqual(
            len({v["digest"] for v in versions}),
            len(versions),
        )
        for version in versions:
            self.assertEqual(len(version["files"]), 2)

    def test_partitioned_import_is_order_independent(self):
        reference = run_import(self.root)
        rng = random.Random(99)
        names = list(self.written)
        for attempt in range(4):
            rng.shuffle(names)
            target = self.tmp / "order-{}".format(attempt)
            target.mkdir()
            for name in names:
                shutil.copy2(self.root / name, target / name)
            self.assertEqual(run_import(target), reference)


class GenerationAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="imphist-"))
        self.root = self.tmp / "input"
        Fixture(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rows_match_independent_expectation(self):
        result = run_import(self.root)
        # The legacy fixture row (e1) is checked separately below.
        generated = [
            row for row in result["rows"] if row["id"][0] in "abcd"
        ]
        self.assertEqual(generated, EXPECTED_ROWS)

    def test_decimal_overflow_reject_shape(self):
        result = run_import(self.root)
        overflow = [r for r in result["rejects"] if r["reason"] == "DECIMAL_OVERFLOW"]
        self.assertEqual(overflow, [EXPECTED_OVERFLOW_REJECT])
        self.assertNotIn("b2", [row["id"] for row in result["rows"]])

    def test_naive_timestamp_is_isolated_with_field_and_source(self):
        result = run_import(self.root)
        naive = [r for r in result["rejects"] if r["file"] == "naive.parquet"]
        self.assertEqual(len(naive), 1)
        self.assertEqual(naive[0]["row"], 1)
        self.assertEqual(naive[0]["field"], "utc")
        self.assertEqual(naive[0]["source"], "at")
        self.assertEqual(naive[0]["reason"], "TIMESTAMP_SEMANTICS")
        self.assertNotIn("g1", [row["id"] for row in result["rows"]])

    def test_legacy_sidecar_alignment(self):
        result = run_import(self.root)
        legacy = [row for row in result["rows"] if row["id"] == "e1"]
        self.assertEqual(len(legacy), 1)
        self.assertEqual(
            legacy[0],
            {
                "id": "e1",
                "title": "mapped",
                "score": None,
                "items": [{"name": "eur", "amount": "2.68"}],
                "utc": "2026-01-01T12:34:56.789Z",
            },
        )
        source = [s for s in result["sources"] if s["file"] == "legacy.parquet"][0]
        self.assertEqual(source["id_source"], "ids_json")
        self.assertEqual(source["ids_file"], "legacy.ids.json")

    def test_same_name_different_id_is_never_joined(self):
        result = run_import(self.root)
        rows = {row["id"]: row for row in result["rows"]}
        # b1 keeps the renamed column's value, never the decoy label.
        self.assertEqual(rows["b1"]["title"], "kept")
        self.assertEqual(rows["a1"]["title"], "first")
        # gen3 has no ID-2 column at all; the same-named decoy must not fill in.
        self.assertEqual(rows["c1"]["title"], "untitled")
        self.assertEqual(rows["c2"]["title"], "untitled")
        # b2 would also have title=null rather than "decoy-label", but it is
        # isolated for decimal overflow, so it never reaches the rows at all.
        self.assertNotIn("b2", rows)
        self.assertIn(
            "gen2.parquet",
            [r["file"] for r in result["rejects"]],
        )

    def test_ambiguous_file_is_file_level_reject(self):
        result = run_import(self.root)
        ambiguous = [r for r in result["rejects"] if r["file"] == "ambiguous.parquet"]
        self.assertEqual([r["field"] for r in ambiguous], ["id", "same"])
        self.assertTrue(all(r["row"] is None for r in ambiguous))
        self.assertTrue(all(r["reason"] == "AMBIGUOUS_SCHEMA" for r in ambiguous))
        self.assertNotIn("f1", [row["id"] for row in result["rows"]])

    def test_ambiguous_hint_explains_name_reuse(self):
        # The "label" name carries ID 2 in gen1 (and 90 in gen2/gen3), so a
        # name-based join is provably unsafe.  With the ID stripped, the column
        # is unmappable and the diagnosis must say why.
        pq.write_table(
            pa.Table.from_pylist(
                [{"label": "x", "id": "r2"}],
                schema=pa.schema(
                    [pa.field("label", pa.string()), f("id", pa.string(), 1)]
                ),
            ),
            self.root / "relabel.parquet",
        )
        result = run_import(self.root)
        rejects = [r for r in result["rejects"] if r["file"] == "relabel.parquet"]
        self.assertEqual(len(rejects), 1)
        self.assertEqual(rejects[0]["field"], "label")
        self.assertIn("90", rejects[0]["hint"])
        self.assertIn("2", rejects[0]["hint"])
        self.assertIn("re-used", rejects[0]["hint"])

    def test_sources_manifest_is_sorted_and_has_digests(self):
        result = run_import(self.root)
        names = [s["file"] for s in result["sources"]]
        self.assertEqual(names, sorted(names))
        self.assertEqual(names, sorted(p.name for p in self.root.glob("*.parquet")))
        for source in result["sources"]:
            self.assertIn("id_source", source)
            self.assertTrue(source["schema"]["digest"].startswith("sha256:"))
            self.assertTrue(source["schema"]["fields"])

    def test_nested_rename_keeps_ids(self):
        result = run_import(self.root)
        source = [s for s in result["sources"] if s["file"] == "gen3.parquet"][0]
        fields = {entry["path"]: entry for entry in source["schema"]["fields"]}
        self.assertEqual(fields["entries.element.name"]["id"], 5)
        self.assertEqual(fields["entries.element.total"]["id"], 6)
        self.assertIn("entries.element", fields)

    def test_rejects_are_sorted_by_file_then_row(self):
        result = run_import(self.root)
        keys = [
            (r["file"], 0 if r["row"] is None else r["row"])
            for r in result["rejects"]
        ]
        self.assertEqual(keys, sorted(keys))
        for reject in result["rejects"]:
            self.assertTrue({"file", "row", "field", "reason"} <= set(reject))

    def test_manifest_lists_files_and_distinct_schema_versions(self):
        result = run_import(self.root)
        manifest = result["manifest"]
        self.assertEqual(
            manifest["input_files"],
            [s["file"] for s in result["sources"]],
        )
        versions = manifest["schema_versions"]
        digests = [v["digest"] for v in versions]
        self.assertEqual(digests, sorted(digests))
        self.assertEqual(len(digests), len(set(digests)))
        for version in versions:
            self.assertTrue(version["files"])
            self.assertTrue(all(name.endswith(".parquet") for name in version["files"]))
        # gen1..gen4 are four distinct generations, plus legacy and the two
        # diagnostic files.
        covered = sorted(
            name for version in versions for name in version["files"]
        )
        self.assertEqual(
            covered, sorted(p.name for p in self.root.glob("*.parquet"))
        )
        for source in result["sources"]:
            self.assertEqual(source["schema_digest"], source["schema"]["digest"])
            self.assertTrue(source["file_sha256"].startswith("sha256:"))


class OrderIndependenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="imphist-order-"))
        self.base = self.tmp / "inputs"
        Fixture(self.base)
        self.reference = run_import(self.base)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _copy_with_order(self, order, destination):
        destination.mkdir(parents=True)
        for name in order:
            shutil.copy2(self.base / name, destination / name)
        for sidecar in self.base.glob("*.ids.json"):
            shutil.copy2(sidecar, destination / sidecar.name)

    def test_shuffled_file_sets_import_identically(self):
        names = sorted(p.name for p in self.base.glob("*.parquet"))
        rng = random.Random(20260101)
        for attempt in range(6):
            order = names[:]
            rng.shuffle(order)
            target = self.tmp / "shuffle-{}".format(attempt)
            self._copy_with_order(order, target)
            self.assertEqual(run_import(target), self.reference)

    def test_repeated_import_is_stable(self):
        self.assertEqual(run_import(self.base), self.reference)
        self.assertEqual(run_import(self.base), self.reference)


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="imphist-diag-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, schema, rows):
        pq.write_table(
            pa.Table.from_pylist(rows, schema=pa.schema(schema)), self.tmp / name
        )

    def test_mislabeled_timestamp_unit_is_located(self):
        # Declared ms while the epoch integer is microsecond scale: it is out of
        # range for ms but fits us, so the label is inconsistent.
        raw = 1700000000123456
        array = pa.array([raw], type=pa.timestamp("ms", tz="UTC"))
        table = pa.Table.from_arrays(
            [pa.array(["h1"]), array],
            schema=pa.schema(
                [f("id", pa.string(), 1), f("at", pa.timestamp("ms", tz="UTC"), 7)]
            ),
        )
        pq.write_table(table, self.tmp / "mislabeled.parquet")
        result = subject.import_directory(self.tmp)
        rejects = [r for r in result["rejects"] if r["file"] == "mislabeled.parquet"]
        self.assertEqual(len(rejects), 1)
        self.assertEqual(rejects[0]["row"], 1)
        self.assertEqual(rejects[0]["field"], "utc")
        self.assertEqual(rejects[0]["source"], "at")
        self.assertEqual(rejects[0]["reason"], "TIMESTAMP_SEMANTICS")
        self.assertIn("fits us not declared ms", rejects[0]["detail"])
        self.assertEqual(result["rows"], [])

    def test_nanosecond_scale_value_under_millisecond_label(self):
        raw = 1700000000123456789
        array = pa.array([raw], type=pa.timestamp("ms", tz="UTC"))
        table = pa.Table.from_arrays(
            [pa.array(["h2"]), array],
            schema=pa.schema(
                [f("id", pa.string(), 1), f("at", pa.timestamp("ms", tz="UTC"), 7)]
            ),
        )
        pq.write_table(table, self.tmp / "nanos-label.parquet")
        result = subject.import_directory(self.tmp)
        rejects = [
            r for r in result["rejects"] if r["file"] == "nanos-label.parquet"
        ]
        self.assertEqual(len(rejects), 1)
        self.assertEqual(rejects[0]["field"], "utc")
        self.assertIn("fits ns not declared ms", rejects[0]["detail"])

    def test_decimal_boundary_rounds_half_even_then_overflows(self):
        rows = [
            {"id": "i1", "amount": Decimal("9999.994")},
            {"id": "i2", "amount": Decimal("9999.995")},
            {"id": "i3", "amount": Decimal("-9999.995")},
            {"id": "i4", "amount": Decimal("10000.005")},
        ]
        self._write(
            "overflow.parquet",
            [f("id", pa.string(), 1), f("amount", DEC20, 3)],
            rows,
        )
        result = subject.import_directory(self.tmp)
        self.assertEqual([row["score"] for row in result["rows"]], ["9999.99"])
        rejects = [r for r in result["rejects"] if r["file"] == "overflow.parquet"]
        # 9999.995 -> 10000.00 and -9999.995 -> -10000.00 overflow after
        # rounding; 10000.005 is plainly out of range.
        self.assertEqual([r["row"] for r in rejects], [2, 3, 4])
        self.assertTrue(all(r["field"] == "score" for r in rejects))
        self.assertTrue(all(r["source"] == "amount" for r in rejects))
        self.assertTrue(all(r["reason"] == "DECIMAL_OVERFLOW" for r in rejects))

    def test_missing_ids_are_reported_per_column(self):
        self._write(
            "noids.parquet",
            [pa.field("name", pa.string()), pa.field("value", pa.string())],
            [{"name": "j1", "value": "?"}],
        )
        result = subject.import_directory(self.tmp)
        rejects = [r for r in result["rejects"] if r["file"] == "noids.parquet"]
        self.assertEqual([r["field"] for r in rejects], ["name", "value"])
        self.assertTrue(all(r["row"] is None for r in rejects))
        self.assertTrue(all(r["reason"] == "AMBIGUOUS_SCHEMA" for r in rejects))
        self.assertEqual(result["rows"], [])

    def test_partial_ids_resolved_by_sidecar(self):
        self._write(
            "partial.parquet",
            [f("id", pa.string(), 1), pa.field("extra", pa.string())],
            [{"id": "k1", "extra": "x"}],
        )
        (self.tmp / "partial.ids.json").write_text(
            json.dumps({"extra": 90}), encoding="utf-8"
        )
        result = subject.import_directory(self.tmp)
        self.assertEqual(result["rows"][0]["id"], "k1")
        self.assertFalse(
            [r for r in result["rejects"] if r["file"] == "partial.parquet"]
        )

    def test_duplicate_field_id_is_ambiguous(self):
        self._write(
            "dupe.parquet",
            [f("a", pa.string(), 2), f("b", pa.string(), 2), f("id", pa.string(), 1)],
            [{"a": "one", "b": "two", "id": "m1"}],
        )
        result = subject.import_directory(self.tmp)
        rejects = [r for r in result["rejects"] if r["file"] == "dupe.parquet"]
        self.assertEqual(sorted(r["field"] for r in rejects), ["a", "b"])
        self.assertTrue(all(r["reason"] == "AMBIGUOUS_SCHEMA" for r in rejects))

    def test_empty_list_null_and_missing_are_distinct(self):
        self._write(
            "lists.parquet",
            [
                f("id", pa.string(), 1),
                f(
                    "entries",
                    pa.list_(
                        pa.struct([f("nm", pa.string(), 5), f("amt", DEC, 6)])
                    ),
                    4,
                ),
            ],
            [{"id": "n1", "entries": []}, {"id": "n2", "entries": None}],
        )
        self._write("nolists.parquet", [f("id", pa.string(), 1)], [{"id": "n3"}])
        result = subject.import_directory(self.tmp)
        rows = {row["id"]: row for row in result["rows"]}
        self.assertEqual(rows["n1"]["items"], [])
        self.assertIsNone(rows["n2"]["items"])
        self.assertIsNone(rows["n3"]["items"])

    def test_unsupported_items_shape_is_file_level_reject(self):
        # A list of lists has no <name, amount> projection.
        self._write(
            "nested.parquet",
            [
                f("id", pa.string(), 1),
                f(
                    "entries",
                    pa.list_(
                        pa.list_(
                            pa.struct(
                                [f("nm", pa.string(), 5), f("amt", DEC, 6)]
                            )
                        )
                    ),
                    4,
                ),
            ],
            [{"id": "w1", "entries": [[{"nm": "x", "amt": None}]]}],
        )
        result = subject.import_directory(self.tmp)
        rejects = [r for r in result["rejects"] if r["file"] == "nested.parquet"]
        shape = [r for r in rejects if "UNSUPPORTED_ITEMS_SHAPE" in r["detail"]]
        self.assertEqual(len(shape), 1)
        self.assertIsNone(shape[0]["row"])
        self.assertEqual(shape[0]["field"], "entries")
        self.assertEqual(shape[0]["reason"], "AMBIGUOUS_SCHEMA")
        self.assertTrue(all(r["row"] is None for r in rejects))
        self.assertEqual(result["rows"], [])

    def test_scalar_list_without_id_5_is_file_level_reject(self):
        self._write(
            "plainlist.parquet",
            [f("id", pa.string(), 1), f("tags", pa.list_(pa.string()), 4)],
            [{"id": "w2", "tags": ["a", "b"]}],
        )
        result = subject.import_directory(self.tmp)
        rejects = [r for r in result["rejects"] if r["file"] == "plainlist.parquet"]
        shape = [r for r in rejects if "no field ID 5" in r["detail"]]
        self.assertEqual(len(shape), 1)
        self.assertEqual(shape[0]["field"], "tags")
        self.assertTrue(all(r["row"] is None for r in rejects))
        self.assertEqual(result["rows"], [])

    def test_negative_amount_rounding_to_zero(self):
        self._write(
            "negzero.parquet",
            [
                f("id", pa.string(), 1),
                f(
                    "entries",
                    pa.list_(
                        pa.struct([f("nm", pa.string(), 5), f("amt", DEC, 6)])
                    ),
                    4,
                ),
            ],
            [
                {
                    "id": "v1",
                    "entries": [{"nm": "y", "amt": Decimal("-0.004")}],
                }
            ],
        )
        result = subject.import_directory(self.tmp)
        self.assertEqual(
            result["rows"][0]["items"], [{"name": "y", "amount": "0.00"}]
        )
        self.assertEqual(result["rejects"], [])

    def test_struct_element_without_ids_5_6_yields_null_projections(self):
        # Recognized-but-unrelated child IDs are ignored, so elements project
        # to an explicit {name: null, amount: null} rather than disappearing.
        self._write(
            "nochildids.parquet",
            [
                f("id", pa.string(), 1),
                f(
                    "entries",
                    pa.list_(
                        pa.struct([f("x", pa.string(), 33), f("y", DEC, 34)])
                    ),
                    4,
                ),
            ],
            [{"id": "y1", "entries": [{"x": "a", "y": Decimal("1.000")}]}],
        )
        result = subject.import_directory(self.tmp)
        self.assertEqual(
            result["rows"][0]["items"], [{"name": None, "amount": None}]
        )
        self.assertEqual(result["rejects"], [])

    def test_struct_element_children_without_ids_are_ambiguous(self):
        # Unknown columns inside the element cannot be identified, and the
        # contract forbids falling back to names.
        self._write(
            "unidentified.parquet",
            [
                f("id", pa.string(), 1),
                f(
                    "entries",
                    pa.list_(
                        pa.struct([pa.field("x", pa.string()), pa.field("y", DEC)])
                    ),
                    4,
                ),
            ],
            [{"id": "y3", "entries": [{"x": "a", "y": Decimal("1.000")}]}],
        )
        result = subject.import_directory(self.tmp)
        rejects = [
            r for r in result["rejects"] if r["file"] == "unidentified.parquet"
        ]
        self.assertEqual(
            [r["field"] for r in rejects],
            ["entries.element.x", "entries.element.y"],
        )
        self.assertEqual(result["rows"], [])

    def test_ignored_id_columns_do_not_change_rows(self):
        self._write(
            "extra.parquet",
            [
                f("id", pa.string(), 1),
                f("renamed-title", pa.string(), 2),
                f("old-name", pa.string(), 33),
            ],
            [{"id": "y2", "renamed-title": "t", "old-name": "ignored"}],
        )
        result = subject.import_directory(self.tmp)
        self.assertEqual(result["rows"][0]["title"], "t")
        self.assertEqual(result["rejects"], [])

    def test_rows_without_id_sort_last(self):
        self._write(
            "m1.parquet",
            [f("id", pa.string(), 1), f("t", pa.string(), 2)],
            [{"id": "zz", "t": "has-id"}],
        )
        self._write(
            "m2.parquet",
            [f("id", pa.string(), 1), f("t", pa.string(), 2)],
            [{"id": None, "t": "no-id"}],
        )
        result = subject.import_directory(self.tmp)
        self.assertEqual(
            [row["title"] for row in result["rows"]], ["has-id", "no-id"]
        )

    def test_non_string_id_and_float_score_are_row_rejects(self):
        self._write(
            "types.parquet",
            [f("id", pa.int64(), 1)],
            [{"id": 5}, {"id": 6}],
        )
        self._write(
            "float.parquet",
            [f("id", pa.string(), 1), f("s", pa.float64(), 3)],
            [{"id": "f1", "s": 1.5}],
        )
        result = subject.import_directory(self.tmp)
        ids = [r for r in result["rejects"] if r["file"] == "types.parquet"]
        self.assertEqual([r["row"] for r in ids], [1, 2])
        self.assertTrue(all(r["reason"] == "TYPE_MISMATCH" for r in ids))
        self.assertTrue(all(r["field"] == "id" for r in ids))
        floats = [r for r in result["rejects"] if r["file"] == "float.parquet"]
        self.assertEqual(len(floats), 1)
        self.assertEqual(floats[0]["field"], "score")
        self.assertEqual(floats[0]["reason"], "TYPE_MISMATCH")
        self.assertEqual(result["rows"], [])

    def test_utc_wrong_type_is_row_reject(self):
        self._write(
            "badtime.parquet",
            [f("id", pa.string(), 1), f("when", pa.string(), 7)],
            [{"id": "t9", "when": "2026-01-01"}],
        )
        result = subject.import_directory(self.tmp)
        rejects = [r for r in result["rejects"] if r["file"] == "badtime.parquet"]
        self.assertEqual(len(rejects), 1)
        self.assertEqual(rejects[0]["field"], "utc")
        self.assertEqual(rejects[0]["reason"], "TIMESTAMP_SEMANTICS")
        self.assertEqual(result["rows"], [])

    def test_null_element_inside_list_is_preserved(self):
        self._write(
            "nulls.parquet",
            [
                f("id", pa.string(), 1),
                f(
                    "entries",
                    pa.list_(
                        pa.struct([f("nm", pa.string(), 5), f("amt", DEC, 6)])
                    ),
                    4,
                ),
            ],
            [
                {
                    "id": "o1",
                    "entries": [
                        {"nm": "x", "amt": Decimal("0.005")},
                        None,
                        {"nm": None, "amt": None},
                    ],
                }
            ],
        )
        result = subject.import_directory(self.tmp)
        self.assertEqual(
            result["rows"][0]["items"],
            [{"name": "x", "amount": "0.00"}, None, {"name": None, "amount": None}],
        )


class DecimalAndListEdgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="imphist-values-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_table(self, name, table):
        pq.write_table(table, self.tmp / name)

    def test_scale_expansion_from_integer_decimal_and_int_column(self):
        self._write_table(
            "scales.parquet",
            pa.Table.from_pylist(
                [
                    {"id": "s1", "a": Decimal("7"), "ignored": 7},
                    {"id": "s2", "a": Decimal("-0"), "ignored": -12},
                ],
                schema=pa.schema(
                    [
                        f("id", pa.string(), 1),
                        f("a", pa.decimal128(6, 0), 3),
                        f("ignored", pa.int32(), 90),
                    ]
                ),
            ),
        )
        self._write_table(
            "intscore.parquet",
            pa.Table.from_pylist(
                [{"id": "s3", "a": 7}],
                schema=pa.schema([f("id", pa.string(), 1), f("a", pa.int64(), 3)]),
            ),
        )
        result = subject.import_directory(self.tmp)
        self.assertEqual(result["rejects"], [])
        rows = {row["id"]: row for row in result["rows"]}
        self.assertEqual(rows["s1"]["score"], "7.00")
        self.assertEqual(rows["s2"]["score"], "0.00")
        self.assertEqual(rows["s3"]["score"], "7.00")

    def test_scale_contraction_rounds_half_even(self):
        self._write_table(
            "contract.parquet",
            pa.Table.from_pylist(
                [
                    {"id": "t1", "a": Decimal("0.1250")},
                    {"id": "t2", "a": Decimal("0.1350")},
                    {"id": "t3", "a": Decimal("-0.1250")},
                ],
                schema=pa.schema(
                    [f("id", pa.string(), 1), f("a", pa.decimal128(10, 4), 3)]
                ),
            ),
        )
        result = subject.import_directory(self.tmp)
        scores = [row["score"] for row in result["rows"]]
        self.assertEqual(scores, ["0.12", "0.14", "-0.12"])

    def test_ignored_nested_columns_are_not_materialized(self):
        item_struct = pa.struct(
            [
                f("nm", pa.string(), 5),
                f("amt", DEC, 6),
                f("stamp", pa.timestamp("ns", tz="UTC"), 91),
            ]
        )
        # 8e18 ns is a legal int64 but cannot become a datetime, so to_pylist()
        # on that column would raise; the importer must never touch it.
        stamps = pa.array(
            [8_000_000_000_000_000_000], type=pa.timestamp("ns", tz="UTC")
        )
        struct_values = pa.StructArray.from_arrays(
            [
                pa.array(["x"]),
                pa.array([Decimal("1.005")], type=DEC),
                stamps,
            ],
            fields=list(item_struct),
        )
        list_values = pa.ListArray.from_arrays(
            pa.array([0, 1], type=pa.int32()), struct_values
        )
        table = pa.Table.from_arrays(
            [pa.array(["u1"]), list_values],
            schema=pa.schema(
                [f("id", pa.string(), 1), f("entries", pa.list_(item_struct), 4)]
            ),
        )
        self._write_table("ignored.parquet", table)
        result = subject.import_directory(self.tmp)
        self.assertEqual(
            result["rows"],
            [
                {
                    "id": "u1",
                    "title": "untitled",
                    "score": None,
                    "items": [{"name": "x", "amount": "1.00"}],
                    "utc": None,
                }
            ],
        )
        self.assertEqual(result["rejects"], [])

    def test_rows_with_same_id_are_ordered_by_file_then_row(self):
        for name, value in (("z.parquet", "same"), ("a.parquet", "same")):
            self._write_table(
                name,
                pa.Table.from_pylist(
                    [{"id": value, "t": name}],
                    schema=pa.schema([f("id", pa.string(), 1), f("t", pa.string(), 2)]),
                ),
            )
        result = subject.import_directory(self.tmp)
        self.assertEqual([row["title"] for row in result["rows"]], ["a.parquet", "z.parquet"])


class SidecarValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="imphist-sidecar-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, schema, rows):
        pq.write_table(
            pa.Table.from_pylist(rows, schema=pa.schema(schema)), self.tmp / name
        )

    def _reject_fields(self, file_name):
        result = subject.import_directory(self.tmp)
        return [
            r["field"] for r in result["rejects"] if r["file"] == file_name
        ]

    def test_sidecar_conflicting_with_embedded_id(self):
        self._write(
            "conflict.parquet",
            [f("id", pa.string(), 1), pa.field("t", pa.string())],
            [{"id": "p1", "t": "x"}],
        )
        (self.tmp / "conflict.ids.json").write_text(
            json.dumps({"id": 99, "t": 2}), encoding="utf-8"
        )
        self.assertEqual(self._reject_fields("conflict.parquet"), ["id"])

    def test_sidecar_unknown_path(self):
        self._write("missing.parquet", [pa.field("x", pa.string())], [{"x": "y"}])
        (self.tmp / "missing.ids.json").write_text(
            json.dumps({"nope": 1}), encoding="utf-8"
        )
        # Both the bogus mapping key and the still-unmapped column are named.
        self.assertEqual(self._reject_fields("missing.parquet"), ["nope", "x"])

    def test_invalid_sidecar_json(self):
        self._write("bad.parquet", [pa.field("x", pa.string())], [{"x": "y"}])
        (self.tmp / "bad.ids.json").write_text("{not json", encoding="utf-8")
        result = subject.import_directory(self.tmp)
        rejects = [r for r in result["rejects"] if r["file"] == "bad.parquet"]
        self.assertTrue(rejects)
        self.assertTrue(all(r["row"] is None for r in rejects))
        self.assertTrue(all(r["reason"] == "AMBIGUOUS_SCHEMA" for r in rejects))

    def test_literal_dotted_column_name_resolves_exactly(self):
        # A physical column literally named "note.text" is addressed by the
        # exact sidecar key, not split into a struct path.
        self._write(
            "literal.parquet",
            [pa.field("id", pa.string()), pa.field("note.text", pa.string())],
            [{"id": "z1", "note.text": "literal"}],
        )
        (self.tmp / "literal.ids.json").write_text(
            json.dumps({"id": 1, "note.text": 2}), encoding="utf-8"
        )
        result = subject.import_directory(self.tmp)
        self.assertEqual(result["rows"][0]["id"], "z1")
        self.assertEqual(result["rows"][0]["title"], "literal")
        self.assertEqual(result["rejects"], [])

    def test_literal_dotted_name_colliding_with_struct_path_is_ambiguous(self):
        struct_type = pa.struct([pa.field("text", pa.string())])
        self._write(
            "collide.parquet",
            [
                pa.field("id", pa.string()),
                pa.field("note", struct_type),
                pa.field("note.text", pa.string()),
            ],
            [{"id": "z2", "note": {"text": "inner"}, "note.text": "outer"}],
        )
        (self.tmp / "collide.ids.json").write_text(
            json.dumps({"id": 1, "note.text": 2}), encoding="utf-8"
        )
        result = subject.import_directory(self.tmp)
        rejects = [r for r in result["rejects"] if r["file"] == "collide.parquet"]
        self.assertTrue(rejects)
        details = " ".join(r["detail"] for r in rejects)
        self.assertIn("MAPPING_AMBIGUOUS_PATH", details)
        self.assertEqual(result["rows"], [])

    def test_nested_sidecar_alias_resolves(self):
        self._write(
            "nested.parquet",
            [
                pa.field("id", pa.string()),
                pa.field(
                    "entries",
                    pa.list_(
                        pa.struct(
                            [
                                pa.field("nm", pa.string()),
                                pa.field("amt", pa.decimal128(14, 3)),
                            ]
                        )
                    ),
                ),
            ],
            [{"id": "q1", "entries": [{"nm": "gbp", "amt": Decimal("1.005")}]}],
        )
        (self.tmp / "nested.ids.json").write_text(
            json.dumps({"id": 1, "entries": 4, "entries.nm": 5, "entries.amt": 6}),
            encoding="utf-8",
        )
        result = subject.import_directory(self.tmp)
        rows = [row for row in result["rows"] if row["id"] == "q1"]
        self.assertEqual(rows[0]["items"], [{"name": "gbp", "amount": "1.00"}])
        source = [s for s in result["sources"] if s["file"] == "nested.parquet"][0]
        self.assertEqual(source["id_source"], "ids_json")
        self.assertFalse(
            [r for r in result["rejects"] if r["file"] == "nested.parquet"]
        )


if __name__ == "__main__":
    unittest.main()
