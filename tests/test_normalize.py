"""Unit tests for versioned normalization and every rejection rule."""

import unittest
from decimal import Decimal

from ndjson_pipe.decoder import RawLine
from ndjson_pipe.normalize import Normalized, Reject, normalize_line

from tests.corpus import BAD_UTF8_BYTES, FIXED_REJECTS


def line(raw: bytes, line_no: int = 1) -> RawLine:
    return RawLine(line_no=line_no, start_offset=0, end_offset=len(raw) + 1, raw=raw)


class NormalizeHappyPathTests(unittest.TestCase):
    def test_v1_decimal_string_exact(self):
        n = normalize_line(line(b'{"version":1,"id":"A-01","amount":"12.35","currency":"CNY","note":"\xe8\x93\x9d\xe8\x8e\x93"}'))
        self.assertIsInstance(n.amount, Decimal)
        self.assertEqual(str(n.amount), "12.35")
        self.assertEqual(n.amount_cents, 1235)
        self.assertEqual(n.currency, "CNY")
        self.assertEqual(n.note_present, True)
        self.assertEqual(n.note_value, "蓝莓")
        self.assertEqual(n.normalized_by, 1)

    def test_v2_integer_cents(self):
        n = normalize_line(line(b'{"version":2,"id":"A-02","amount_cents":275,"currency":"CNY","note":null}'))
        self.assertEqual(str(n.amount), "2.75")
        self.assertEqual(n.amount_cents, 275)
        self.assertTrue(n.note_present)
        self.assertIsNone(n.note_value)

    def test_missing_note_distinct_from_null_note(self):
        missing = normalize_line(line(b'{"version":1,"id":"A-03","amount":"0.09","currency":"CNY"}'))
        explicit_null = normalize_line(line(b'{"version":1,"id":"A-04","amount":"0.09","currency":"CNY","note":null}'))
        self.assertFalse(missing.note_present)
        self.assertTrue(explicit_null.note_present)
        self.assertIsNone(explicit_null.note_value)
        self.assertNotEqual(missing.as_tuple(), explicit_null.as_tuple())

    def test_amount_padding_is_exact_decimal(self):
        n = normalize_line(line(b'{"version":1,"id":"X","amount":"7","currency":"USD"}'))
        self.assertEqual(str(n.amount), "7.00")
        n2 = normalize_line(line(b'{"version":2,"id":"X","amount_cents":5,"currency":"USD"}'))
        self.assertEqual(str(n2.amount), "0.05")
        self.assertEqual(n2.amount_cents, 5)

    def test_negative_and_zero_amounts(self):
        n = normalize_line(line(b'{"version":1,"id":"X","amount":"-3.40","currency":"EUR"}'))
        self.assertEqual(n.amount_cents, -340)
        zero = normalize_line(line(b'{"version":2,"id":"X","amount_cents":0,"currency":"EUR"}'))
        self.assertEqual(str(zero.amount), "0.00")

    def test_extra_fields_ignored(self):
        n = normalize_line(line(b'{"version":1,"id":"X","amount":"1.00","currency":"CNY","future":{"x":[1,2]}}'))
        self.assertEqual(n.record_id, "X")

    def test_money_never_float(self):
        n = normalize_line(line(b'{"version":2,"id":"X","amount_cents":199,"currency":"CNY"}'))
        self.assertEqual(n.amount, Decimal("1.99"))
        self.assertNotIsInstance(n.amount, float)


class RejectionRuleTests(unittest.TestCase):
    def test_every_fixed_sample_gets_its_documented_reason(self):
        for label, raw, expected_reason in FIXED_REJECTS:
            with self.subTest(label=label):
                with self.assertRaises(Reject) as ctx:
                    normalize_line(line(raw))
                self.assertEqual(ctx.exception.reason, expected_reason)
                self.assertTrue(ctx.exception.to_struct()["reason"])

    def test_invalid_utf8(self):
        with self.assertRaises(Reject) as ctx:
            normalize_line(line(BAD_UTF8_BYTES))
        self.assertEqual(ctx.exception.reason, "encoding_error")

    def test_precision_overflow_not_silently_rounded(self):
        with self.assertRaises(Reject) as ctx:
            normalize_line(line(b'{"version":1,"id":"X","amount":"1.999","currency":"CNY"}'))
        self.assertEqual(ctx.exception.reason, "amount_precision")

    def test_duplicate_key_reports_field(self):
        with self.assertRaises(Reject) as ctx:
            normalize_line(line(b'{"version":1,"id":"a","id":"b","amount":"1.00","currency":"CNY"}'))
        self.assertEqual(ctx.exception.reason, "duplicate_key")
        self.assertEqual(ctx.exception.field, "id")

    def test_boolean_is_not_integer(self):
        with self.assertRaises(Reject) as ctx:
            normalize_line(line(b'{"version":2,"id":"X","amount_cents":true,"currency":"CNY"}'))
        self.assertEqual(ctx.exception.reason, "bad_type")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
