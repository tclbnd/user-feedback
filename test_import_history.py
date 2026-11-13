from decimal import Decimal
from pathlib import Path
import json
import random
import shutil
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

from make_demo import files as make_demo_files
from import_history import AMBIGUOUS_SCHEMA, import_files, import_path


def field(name, kind, field_id=None):
    metadata = None
    if field_id is not None:
        metadata = {b"PARQUET:field_id": str(field_id).encode()}
    return pa.field(name, kind, metadata=metadata)


def write_table(path, fields, rows):
    pq.write_table(pa.Table.from_pylist(rows, schema=pa.schema(fields)), path)


def reject_index(result):
    return {
        (item["file"], item["row"], item["field"], item["reason"])
        for item in result["rejects"]
    }


class ImportHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir="."))
        self.input = self.tmp / "input"
        self.input.mkdir()
        make_demo_files(self.input)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def result(self):
        return import_path(self.input)

    def test_four_schema_generations_and_independent_expected_rows(self):
        result = self.result()
        expected = [
            {
                "id": "five",
                "title": "untitled",
                "score": None,
                "items": None,
                "utc": None,
            },
            {
                "id": "four",
                "title": "untitled",
                "score": None,
                "items": [],
                "utc": "2026-01-01T00:00:00Z",
            },
            {
                "id": "one",
                "title": "original",
                "score": "7.26",
                "items": None,
                "utc": None,
            },
            {
                "id": "two",
                "title": "kept",
                "score": "7.24",
                "items": [
                    {"name": "usd", "amount": "1.26"},
                    None,
                ],
                "utc": None,
            },
        ]
        self.assertEqual(result["rows"], expected)

        rejects = reject_index(result)
        self.assertIn(
            ("legacy.parquet", None, "id", "AMBIGUOUS_SCHEMA"), rejects
        )
        self.assertIn(
            ("legacy.parquet", None, "same", "AMBIGUOUS_SCHEMA"), rejects
        )
        self.assertIn(
            ("v2.parquet", 2, "score", "DECIMAL_OVERFLOW"), rejects
        )
        self.assertIn(
            ("v4.parquet", 1, "utc", "TIMESTAMP_SEMANTICS"), rejects
        )

        files_by_name = {item["file"]: item for item in result["sources"]}
        self.assertEqual(
            [item["file"] for item in result["sources"]],
            sorted(files_by_name),
        )
        self.assertEqual(
            files_by_name["v2.parquet"]["physical_rows"], 2
        )
        self.assertEqual(
            files_by_name["v2.parquet"]["accepted_rows"], 1
        )
        self.assertEqual(
            files_by_name["v2.parquet"]["rejected_rows"], 1
        )
        versions = {
            item["file"]: item["schema_version"]
            for item in result["sources"]
        }
        self.assertTrue(
            all(version.startswith("sha256:") for version in versions.values())
        )
        self.assertEqual(len(set(versions.values())), 5)

    def test_import_is_repeatable_with_random_file_order(self):
        first = self.result()
        files = list(self.input.glob("*.parquet"))
        rng = random.Random(20261113)
        rng.shuffle(files)
        shuffled = import_files(files)
        self.assertEqual(first, shuffled)

        second_directory_import = self.result()
        self.assertEqual(first, second_directory_import)

    def test_sidecar_uses_literal_paths_and_distinguishes_missing_null_and_empty(self):
        sidecar = {
            "record_id": 1,
            # A literal physical name containing a dot must not be split.
            "display.name": 2,
            "nested": 4,
            "nested.line": 5,
            "nested.value": 6,
        }
        fields = [
            field("record_id", pa.string()),
            field("display.name", pa.string()),
            field(
                "nested",
                pa.list_(
                    pa.struct(
                        [
                            field("line", pa.string()),
                            field("value", pa.decimal128(6, 3)),
                        ]
                    )
                ),
            ),
            field("unknown.ignored", pa.string(), 99),
        ]
        rows = [
            {
                "record_id": "sidecar-1",
                "display.name": "explicit",
                "nested": [
                    {"line": "first", "value": Decimal("1.005")},
                    None,
                ],
                "unknown.ignored": "ignored by target ID",
            },
            {
                "record_id": "sidecar-2",
                "display.name": None,
                "nested": [],
                "unknown.ignored": None,
            },
            {
                "record_id": "sidecar-3",
                # Arrow materializes this as an explicit null because the
                # physical column exists; the title default must not apply.
                "nested": None,
                "unknown.ignored": None,
            },
        ]
        write_table(self.input / "mapped.parquet", fields, rows)
        (self.input / "mapped.ids.json").write_text(
            json.dumps(sidecar), encoding="utf-8"
        )
        ambiguous_fields = [
            field("record_id", pa.string()),
            field("display.name", pa.string()),
            field("unknown.names", pa.string()),
        ]
        write_table(
            self.input / "ambiguous-name.parquet",
            ambiguous_fields,
            [{"record_id": "ambiguous", "display.name": "x", "unknown.names": "y"}],
        )
        result = import_path(self.input)
        ambiguous_rejects = [
            item
            for item in result["rejects"]
            if item["file"] == "ambiguous-name.parquet"
        ]
        self.assertTrue(
            all(item["reason"] == AMBIGUOUS_SCHEMA for item in ambiguous_rejects)
        )
        self.assertEqual(
            {
                (item["row"], item["field"])
                for item in ambiguous_rejects
            },
            {
                (None, "record_id"),
                (None, "display.name"),
                (None, "unknown.names"),
            },
        )
        mapped = [
            row for row in result["rows"] if row["id"].startswith("sidecar")
        ]
        self.assertEqual(
            mapped,
            [
                {
                    "id": "sidecar-1",
                    "title": "explicit",
                    "score": None,
                    "items": [{"name": "first", "amount": "1.00"}, None],
                    "utc": None,
                },
                {
                    "id": "sidecar-2",
                    "title": None,
                    "score": None,
                    "items": [],
                    "utc": None,
                },
                {
                    "id": "sidecar-3",
                    # The column exists, so this is an explicit null rather
                    # than a missing field; the title default must not apply.
                    "title": None,
                    "score": None,
                    "items": None,
                    "utc": None,
                },
            ],
        )
        source = next(
            item for item in result["sources"] if item["file"] == "mapped.parquet"
        )
        self.assertEqual(source["id_mapping"], "mixed")
        self.assertEqual(source["rejected_rows"], 0)
        ambiguous_source = next(
            item
            for item in result["sources"]
            if item["file"] == "ambiguous-name.parquet"
        )
        self.assertEqual(ambiguous_source["accepted_rows"], 0)
        self.assertEqual(ambiguous_source["rejected_rows"], 1)

    def test_missing_column_uses_default_but_explicit_null_is_preserved(self):
        fields = [field("record_id", pa.string())]
        write_table(
            self.input / "default-only.parquet",
            fields,
            [{"record_id": "default-row"}],
        )
        (self.input / "default-only.ids.json").write_text(
            json.dumps({"record_id": 1}), encoding="utf-8"
        )

        result = import_path(self.input)
        row = next(
            item for item in result["rows"] if item["id"] == "default-row"
        )
        self.assertEqual(row["title"], "untitled")
        self.assertIsNone(row["score"])
        self.assertIsNone(row["items"])
        self.assertIsNone(row["utc"])

    def test_decimal_widening_and_narrowing_use_half_even(self):
        fields = [
            field("id", pa.string(), 1),
            field("high_precision", pa.decimal128(8, 3), 3),
            field(
                "items",
                pa.list_(
                    pa.struct(
                        [
                            field("name", pa.string(), 5),
                            field("amount", pa.decimal128(6, 4), 6),
                        ]
                    )
                ),
                4,
            ),
        ]
        rows = [
            {
                "id": "decimal-round-up",
                "high_precision": Decimal("1.235"),
                "items": [
                    {"name": "widened", "amount": Decimal("2.5000")},
                ],
            },
            {
                "id": "decimal-round-down",
                "high_precision": Decimal("-1.235"),
                "items": [
                    {"name": "narrowed", "amount": Decimal("-2.3456")},
                ],
            },
        ]
        write_table(self.input / "decimals.parquet", fields, rows)

        result = import_path(self.input)
        decimal_rows = {
            row["id"]: row
            for row in result["rows"]
            if row["id"].startswith("decimal")
        }
        self.assertEqual(
            decimal_rows["decimal-round-up"]["score"], "1.24"
        )
        self.assertEqual(
            decimal_rows["decimal-round-up"]["items"][0]["amount"], "2.50"
        )
        self.assertEqual(
            decimal_rows["decimal-round-down"]["score"], "-1.24"
        )
        self.assertEqual(
            decimal_rows["decimal-round-down"]["items"][0]["amount"],
            "-2.35",
        )

    def test_timestamp_units_are_normalized_to_utc_with_fraction_width(self):
        unit_specs = [
            ("s", pa.timestamp("s", tz="UTC"), "1970-01-01T00:00:01Z"),
            (
                "ms",
                pa.timestamp("ms", tz="UTC"),
                "1970-01-01T00:00:00.120Z",
            ),
            (
                "us",
                pa.timestamp("us", tz="UTC"),
                "1970-01-01T00:00:00.120000Z",
            ),
            (
                "ns",
                pa.timestamp("ns", tz="UTC"),
                "1970-01-01T00:00:00.120000000Z",
            ),
            (
                "zone-ms",
                pa.timestamp("ms", tz="+02:00"),
                "1970-01-01T00:00:00.120Z",
            ),
        ]
        offsets = {
            "s": 1,
            "ms": 120,
            "us": 120000,
            "ns": 120000000,
            "zone-ms": 120,
        }
        for unit_name, arrow_type, expected in unit_specs:
            one_file = self.input / f"time-{unit_name}.parquet"
            offset = offsets[unit_name]
            values = pa.array([offset], type=arrow_type)
            id_field = field("id", pa.string(), 1)
            at_field = field("at", arrow_type, 7)
            table = pa.Table.from_arrays(
                [pa.array([f"time-{unit_name}"]), values],
                schema=pa.schema([id_field, at_field]),
            )
            pq.write_table(table, one_file)

            result = import_path(self.input)
            row = next(
                item
                for item in result["rows"]
                if item["id"] == f"time-{unit_name}"
            )
            self.assertEqual(row["utc"], expected)

    def test_required_missing_id_and_invalid_nested_value_produce_field_diagnostics(self):
        fields = [
            field("title", pa.string(), 2),
            field(
                "items",
                pa.list_(
                    pa.struct(
                        [
                            field("name", pa.string(), 5),
                            field("amount", pa.decimal128(8, 2), 6),
                        ]
                    )
                ),
                4,
            ),
        ]
        write_table(
            self.input / "bad-row.parquet",
            fields,
            [
                {
                    "title": "no id",
                    "items": [
                        {"name": "x", "amount": Decimal("99999.99")}
                    ],
                }
            ],
        )
        result = import_path(self.input)
        reasons = reject_index(result)
        self.assertIn(
            ("bad-row.parquet", 1, "id", "MISSING_FIELD"), reasons
        )
        self.assertIn(
            (
                "bad-row.parquet",
                1,
                "items[0].amount",
                "DECIMAL_OVERFLOW",
            ),
            reasons,
        )
        detailed = next(
            item
            for item in result["rejects"]
            if item["file"] == "bad-row.parquet"
            and item["field"] == "items[0].amount"
        )
        self.assertEqual(detailed["source"], "items[0].amount")


if __name__ == "__main__":
    unittest.main()
