import unittest
from decimal import Decimal

from pipeline import normalizer as n


def norm(line, version=1):
    return n.normalize_line(line.encode("utf-8"), version)


class NormalizerTest(unittest.TestCase):
    def test_v1_amount_string(self):
        r = norm('{"version":1,"id":"A","amount":"12.35","currency":"CNY"}')
        self.assertTrue(r.ok)
        self.assertEqual(r.amount, Decimal("12.35"))
        self.assertEqual(r.note_state, n.NOTE_MISSING)

    def test_v2_amount_cents_exact(self):
        r = norm('{"version":2,"id":"A","amount_cents":275,"currency":"CNY"}')
        self.assertTrue(r.ok)
        self.assertEqual(r.amount, Decimal("2.75"))

    def test_amount_precision_reject(self):
        r = norm('{"version":1,"id":"A","amount":"0.001","currency":"CNY"}')
        self.assertFalse(r.ok)
        self.assertEqual(r.reason_code, n.AMOUNT_PRECISION)

    def test_duplicate_key_reject(self):
        r = norm('{"version":1,"id":"A","id":"B","amount":"1.00",'
                 '"currency":"CNY"}')
        self.assertFalse(r.ok)
        self.assertEqual(r.reason_code, n.DUPLICATE_KEY)

    def test_null_and_missing_note_are_distinct(self):
        r_null = norm('{"version":1,"id":"A","amount":"1.00",'
                      '"currency":"CNY","note":null}')
        r_missing = norm('{"version":1,"id":"A","amount":"1.00",'
                         '"currency":"CNY"}')
        self.assertEqual(r_null.note_state, n.NOTE_NULL)
        self.assertEqual(r_missing.note_state, n.NOTE_MISSING)

    def test_missing_and_invalid_fields(self):
        self.assertEqual(norm('{"version":1,"id":"A","amount":"1.00"}')
                         .reason_code, n.MISSING_FIELD)
        self.assertEqual(norm('{"version":2,"id":"A","amount_cents":1.5,'
                              '"currency":"CNY"}').reason_code,
                         n.INVALID_FIELD)
        self.assertEqual(norm('{"version":9,"id":"A","amount":"1.00",'
                              '"currency":"CNY"}').reason_code,
                         n.UNKNOWN_VERSION)

    def test_json_and_utf8_rejects(self):
        self.assertEqual(norm('not json').reason_code, n.INVALID_JSON)
        self.assertEqual(norm('   ').reason_code, n.EMPTY_LINE)
        self.assertEqual(norm('[1,2]').reason_code, n.NOT_AN_OBJECT)
        r = n.normalize_line('{"a":"'.encode("utf-8") + b"\xff\"}", 1)
        self.assertEqual(r.reason_code, n.INVALID_UTF8)

    def test_norm_version_difference(self):
        line = '{"version":1,"id":"A","amount":"1.00"}'
        self.assertEqual(norm(line, 1).reason_code, n.MISSING_FIELD)
        r = norm(line, 2)
        self.assertTrue(r.ok)
        self.assertEqual(r.currency, "CNY")

    def test_unicode_and_exact_decimal(self):
        r = norm('{"version":1,"id":"蓝莓","amount":"99999999999999.99",'
                 '"currency":"CNY","note":"🍇"}')
        self.assertTrue(r.ok)
        self.assertEqual(str(r.amount), "99999999999999.99")


if __name__ == "__main__":
    unittest.main()
