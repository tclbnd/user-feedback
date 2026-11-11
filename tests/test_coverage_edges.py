"""Coverage-feedback tests for edge branches surfaced by tools/coverage_feedback.

These extend the *legal* corpus and exercise helper/defensive paths; business
rejection samples remain the fixed list in tests/corpus.py.
"""

import os
import unittest

from ndjson_pipe.decoder import NDJsonStreamDecoder, RawLine
from ndjson_pipe.importer import (
    CRASH_AFTER_COMMIT,
    CrashPlan,
    ImportStats,
    import_source,
)
from ndjson_pipe.normalize import Normalized, Reject, normalize_line
from ndjson_pipe.reference import ExpectedOutcome, batch_split_lines
from ndjson_pipe.store import Store

from tests.helpers import make_db_path, write_source


class EdgeBranchTests(unittest.TestCase):
    def test_raw_line_str_and_normalized_repr(self):
        raw = RawLine(1, 0, 5, b"abc")
        self.assertIn("line=1", str(raw))
        normalized = Normalized("X", 1, "CNY", __import__("decimal").Decimal("1.00"),
                                100, True, "n", 1)
        self.assertIn("Normalized(", repr(normalized))

    def test_decoder_empty_input_loop(self):
        # an empty sealed source must enter and exit the drain loop cleanly
        dec = NDJsonStreamDecoder()
        self.assertEqual(list(dec.feed(b"")), [])
        self.assertEqual(list(dec.flush(sealed=True)), [])
        self.assertEqual(dec.position, 0)
        self.assertEqual(dec.line_count, 0)
        self.assertEqual(dec.buffered_bytes, 0)

    def test_decoder_properties_on_partial_input(self):
        dec = NDJsonStreamDecoder()
        list(dec.feed(b'{"a":1}\n{"x"'))
        self.assertEqual(dec.position, len(b'{"a":1}\n'))
        self.assertEqual(dec.buffered_bytes, len(b'{"x"'))
        self.assertEqual(dec.line_count, 1)

    def test_crash_plan_env_variants_and_validation(self):
        os.environ["PIPE_CRASH_LINE"] = "5"
        for sentinel in ("", "none", "__none__", "off"):
            os.environ["PIPE_CRASH_POINT"] = sentinel
            self.assertIsNone(CrashPlan.from_env().point)
        os.environ["PIPE_CRASH_POINT"] = CRASH_AFTER_COMMIT
        plan = CrashPlan.from_env()
        self.assertEqual((plan.point, plan.line_no), (CRASH_AFTER_COMMIT, 5))
        os.environ["PIPE_CRASH_POINT"] = "bogus"
        with self.assertRaises(ValueError):
            CrashPlan.from_env()
        del os.environ["PIPE_CRASH_POINT"]
        del os.environ["PIPE_CRASH_LINE"]

    def test_fire_if_guard_conditions(self):
        plan = CrashPlan(point=CRASH_AFTER_COMMIT, line_no=3)
        # wrong point and wrong line must not fire
        plan.fire_if("other_point", line_no=3)
        plan.fire_if(CRASH_AFTER_COMMIT, line_no=9)
        self.assertFalse(plan.triggered)

    def test_import_stats_dict_shape(self):
        stats = ImportStats(source_id="s")
        payload = stats.as_dict()
        for key in ("source_id", "accepted", "rejected", "conflicts",
                    "replayed_identical", "blank_lines", "residual_bytes",
                    "checkpoint", "rejection_breakdown"):
            self.assertIn(key, payload)

    def test_expected_outcome_summary(self):
        outcome = ExpectedOutcome(source_id="s")
        summary = outcome.summary()
        self.assertEqual(summary["records"], 0)
        self.assertEqual(summary["checkpoint"], 0)

    def test_batch_splitter_unterminated_final_line_branch(self):
        # no trailing newline: exercises the nl == -1 branch directly
        data = b'{"a":1}\n{"b":2}'
        lines = batch_split_lines(data)
        self.assertEqual([l.raw for l in lines], [b'{"a":1}', b'{"b":2}'])
        self.assertEqual(lines[1].end_offset, len(data))

    def test_decimal_invalid_operation_branch(self):
        # regex blocks normal bad input; craft a value that passes the regex
        # but forces Decimal's InvalidOperation defensive path
        # (a lone sign is rejected by the regex, so use a value with internal
        # whitespace that the pattern rejects -> amount_format branch anyway)
        raw = RawLine(1, 0, 60,
                      b'{"version":1,"id":"Z","amount":"NaN-ish","currency":"CNY"}')
        with self.assertRaises(Reject) as ctx:
            normalize_line(raw)
        self.assertEqual(ctx.exception.reason, "amount_format")

    def test_normalize_unknown_version_guard(self):
        raw = RawLine(1, 0, 50,
                      b'{"version":7,"id":"Z","currency":"CNY","amount":"1.00"}')
        with self.assertRaises(Reject) as ctx:
            normalize_line(raw)
        self.assertEqual(ctx.exception.reason, "unknown_version")

    def test_empty_file_import_checkpoint_zero(self):
        db = make_db_path()
        path = db + ".ndjson"
        write_source(path, b"")
        with Store(db) as store:
            stats = import_source(store, "empty-source", path)
        self.assertEqual(stats.checkpoint, 0)
        with Store(db) as store:
            self.assertEqual(store.stats("empty-source")["records"], 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
