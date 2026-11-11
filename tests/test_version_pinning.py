"""Pinning proof: a simulated future v3 normalizer cannot change v1/v2 rows.

The version gate derives from the normalizer registry, so registering a
version-3 normalizer widens acceptance automatically. Historical rows are
always re-normalized by their own pinned normalizer; the new version field
changes nothing already stored.
"""

import unittest
from decimal import Decimal

from ndjson_pipe import normalize as normalize_module
from ndjson_pipe.decoder import RawLine
from ndjson_pipe.normalize import Reject, normalize_line


def line(raw: bytes) -> RawLine:
    return RawLine(line_no=1, start_offset=0, end_offset=len(raw) + 1, raw=raw)


class VersionPinningProofTests(unittest.TestCase):
    def test_adding_v3_leaves_v1_v2_output_identical(self):
        v1_raw = b'{"version":1,"id":"H-1","amount":"4.20","currency":"CNY"}'
        v2_raw = b'{"version":2,"id":"H-2","amount_cents":420,"currency":"CNY"}'

        before_v1 = normalize_line(line(v1_raw)).as_tuple()
        before_v2 = normalize_line(line(v2_raw)).as_tuple()

        # Simulate a future schema: amount carried as integer micros.
        def _normalize_v3(obj):
            micros = obj["amount_micros"]
            if not isinstance(micros, int) or isinstance(micros, bool):
                raise Reject("bad_type", "amount_micros", "v3 amount must be integer micros")
            amount = (Decimal(micros) / Decimal(1_000_000)).quantize(
                normalize_module.CENT_QUANTUM
            )
            return amount, int((amount * 100).to_integral_value())

        original = dict(normalize_module._NORMALIZERS)
        normalize_module._NORMALIZERS[3] = _normalize_v3
        try:
            v3_raw = b'{"version":3,"id":"H-3","amount_micros":4200000,"currency":"CNY"}'
            v3_result = normalize_line(line(v3_raw))
            self.assertEqual(str(v3_result.amount), "4.20")
            self.assertEqual(v3_result.normalized_by, 3)
            self.assertEqual(v3_result.version, 3)

            # Historical rows re-normalize to byte-identical tuples.
            self.assertEqual(normalize_line(line(v1_raw)).as_tuple(), before_v1)
            self.assertEqual(normalize_line(line(v2_raw)).as_tuple(), before_v2)
        finally:
            normalize_module._NORMALIZERS.clear()
            normalize_module._NORMALIZERS.update(original)

    def test_v3_removed_again_restores_gate(self):
        # after the simulated rollout is unwound, v3 is unknown again
        with self.assertRaises(Reject) as ctx:
            normalize_line(line(b'{"version":3,"id":"X","amount_micros":1,"currency":"CNY"}'))
        self.assertEqual(ctx.exception.reason, "unknown_version")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
