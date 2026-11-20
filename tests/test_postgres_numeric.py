"""PostgreSQL-compatibility tests for the numfn numeric functions.

Every expected value is the text psql prints for the same literal call.
"""
import unittest

from numfn.core import FunctionError
from numfn.registry import FUNCTIONS as F


def call(fn, *args):
    return F[fn](*args)


def fails(fn, *args):
    try:
        F[fn](*args)
    except FunctionError:
        return True
    return False


class IntegerVersusNumericTests(unittest.TestCase):
    def test_integer_arithmetic_stays_integer(self):
        self.assertEqual(call("+", 2, 3), "5")
        self.assertEqual(call("-", 7, 2), "5")
        self.assertEqual(call("*", 6, 7), "42")
        # integer / integer truncates toward zero
        self.assertEqual(call("/", 7, 2), "3")
        self.assertEqual(call("/", -7, 2), "-3")
        self.assertEqual(call("/", 2, 7), "0")

    def test_numeric_arithmetic_keeps_scale(self):
        self.assertEqual(call("/", "7.0", 2), "3.5")
        self.assertEqual(call("+", "1.20", "3.4"), "4.60")
        self.assertEqual(call("-", "10.00", "3.5"), "6.50")
        self.assertEqual(call("*", "19.90", 3), "59.70")
        self.assertEqual(call("*", "1.50", "2.00"), "3.0000")

    def test_integer_modulo_is_truncated(self):
        self.assertEqual(call("%", 17, 5), "2")
        self.assertEqual(call("%", -17, 5), "-2")
        self.assertEqual(call("%", 17, -5), "2")
        self.assertEqual(call("%", -17, -5), "-2")

    def test_numeric_modulo_keeps_max_scale(self):
        self.assertEqual(call("%", "2.50", "2"), "0.50")
        self.assertEqual(call("%", "7.5", "2.0"), "1.5")


class RoundingTests(unittest.TestCase):
    def test_round_half_away_from_zero(self):
        self.assertEqual(call("round", "2.5"), "3")
        self.assertEqual(call("round", "-2.5"), "-3")
        self.assertEqual(call("round", "0.5"), "1")
        self.assertEqual(call("round", "-0.5"), "-1")

    def test_round_with_scale_pads_to_scale(self):
        self.assertEqual(call("round", "1042.4382", 2), "1042.44")
        self.assertEqual(call("round", 42, 2), "42.00")
        self.assertEqual(call("round", "2.45", 1), "2.5")

    def test_round_negative_scale(self):
        self.assertEqual(call("round", "1234.5", -2), "1200")
        self.assertEqual(call("round", "1267.5", -2), "1300")
        self.assertEqual(call("round", "-1234.5", -2), "-1200")

    def test_trunc_truncates_toward_zero(self):
        self.assertEqual(call("trunc", 42.8), "42")
        self.assertEqual(call("trunc", -42.8), "-42")
        self.assertEqual(call("trunc", "42.4382", 2), "42.43")
        self.assertEqual(call("trunc", "1267.5", -2), "1200")
        self.assertEqual(call("trunc", "42.438", -2), "0")

    def test_ceil_floor(self):
        self.assertEqual(call("ceil", -42.8), "-42")
        self.assertEqual(call("floor", -42.8), "-43")
        self.assertEqual(call("ceil", "3.2"), "4")
        self.assertEqual(call("floor", "3.2"), "3")


class DivModTests(unittest.TestCase):
    def test_div_truncates_to_scale_zero(self):
        self.assertEqual(call("div", 8, 3), "2")
        self.assertEqual(call("div", -8, 3), "-2")
        self.assertEqual(call("div", "8.0", 3), "2")

    def test_mod_matches_operator(self):
        self.assertEqual(call("mod", 8, 3), "2")
        self.assertEqual(call("mod", -8, 3), "-2")
        self.assertEqual(call("mod", "8.5", 2), "0.5")


class DivisionScaleTests(unittest.TestCase):
    def test_non_terminating_keeps_sixteen_significant_digits(self):
        self.assertEqual(call("/", "1.0", "3.0"), "0.3333333333333333")
        self.assertEqual(call("/", "100.0", "3.0"), "33.33333333333333")
        self.assertEqual(call("/", "100000.0", "3.0"), "33333.33333333333")
        self.assertEqual(call("/", "2", "7"), "0.2857142857142857")

    def test_terminating_quotient_keeps_natural_scale(self):
        self.assertEqual(call("/", "4.0", "2.0"), "2.0")
        self.assertEqual(call("/", "1.0", "100000.0"), "0.00001")
        self.assertEqual(call("/", "1234567890.12345", "2.5"),
                         "493827156.04938")

    def test_division_by_zero(self):
        self.assertTrue(fails("/", 1, 0))
        self.assertTrue(fails("/", "1.5", "0"))
        self.assertTrue(fails("%", 1, 0))
        self.assertTrue(fails("div", 5, 0))


class RootLogTests(unittest.TestCase):
    def test_sqrt_scale_and_values(self):
        self.assertEqual(call("sqrt", 2), "1.4142135623730950")
        self.assertEqual(call("sqrt", 4), "2.0000000000000000")
        self.assertEqual(call("sqrt", 0), "0.0000000000000000")
        self.assertEqual(call("sqrt", "25.0000"), "5.0000000000000000")

    def test_sqrt_negative_is_error_not_crash(self):
        self.assertTrue(fails("sqrt", -1))
        self.assertTrue(fails("sqrt", "-0.01"))

    def test_ln_and_log(self):
        self.assertEqual(call("ln", 1), "0.0000000000000000")
        self.assertEqual(call("ln", 10), "2.3025850929940457")
        self.assertEqual(call("log", 100), "2.0000000000000000")
        self.assertEqual(call("log", 2, 64), "6.0000000000000000")

    def test_log_domain_errors(self):
        self.assertTrue(fails("ln", 0))
        self.assertTrue(fails("ln", -2))
        self.assertTrue(fails("log", 0))
        self.assertTrue(fails("log", 1, 2))
        self.assertTrue(fails("log", 2, 0))
        self.assertTrue(fails("log", 2, -8))
        self.assertTrue(fails("log", 0, 8))

    def test_large_argument_uses_scale_reduction(self):
        # Beyond float range; the log must still compute, not overflow.
        self.assertTrue(call("ln", "1E300").startswith("690."))


class GcdLcmFactorialTests(unittest.TestCase):
    def test_gcd_integer_and_numeric(self):
        self.assertEqual(call("gcd", 12, 18), "6")
        self.assertEqual(call("gcd", -12, 18), "6")
        self.assertEqual(call("gcd", 0, 5), "5")
        self.assertEqual(call("gcd", 0, 0), "0")
        self.assertEqual(call("gcd", "12.0", "18.0"), "6.0")
        self.assertEqual(call("gcd", "3.5", "2.1"), "0.7")

    def test_lcm_integer_and_numeric(self):
        self.assertEqual(call("lcm", 12, 18), "36")
        self.assertEqual(call("lcm", "12.0", "18.0"), "36.0")
        self.assertEqual(call("lcm", 0, 5), "0")
        self.assertEqual(call("lcm", "0.0", "5.0"), "0.0")

    def test_factorial_boundaries(self):
        self.assertEqual(call("factorial", 0), "1")
        self.assertEqual(call("factorial", 5), "120")
        self.assertEqual(call("factorial", 20), "2432902008176640000")
        self.assertEqual(call("factorial", 21), "51090942171709440000")
        self.assertTrue(fails("factorial", 1001))
        self.assertTrue(fails("factorial", -1))


class WidthBucketTests(unittest.TestCase):
    def test_basic_buckets(self):
        self.assertEqual(call("width_bucket", "37.5", 0, 100, 4), "2")
        self.assertEqual(call("width_bucket", "5.35", "0.024", "10.06", 5),
                         "3")
        self.assertEqual(call("width_bucket", 0, 0, 10, 5), "1")

    def test_outside_range(self):
        self.assertEqual(call("width_bucket", -1, 0, 10, 5), "0")
        self.assertEqual(call("width_bucket", 10, 0, 10, 5), "6")
        self.assertEqual(call("width_bucket", 11, 0, 10, 5), "6")

    def test_reversed_bounds(self):
        self.assertEqual(call("width_bucket", 3, 10, 0, 5), "4")

    def test_bad_arguments(self):
        self.assertTrue(fails("width_bucket", 5, 0, 0, 5))
        self.assertTrue(fails("width_bucket", 5, 0, 10, 0))
        self.assertTrue(fails("width_bucket", 5, 0, 10, -1))


class ScaleTests(unittest.TestCase):
    def test_scale_counts_all_fractional_digits(self):
        self.assertEqual(call("scale", "8.4100"), "4")
        self.assertEqual(call("scale", "100.00"), "2")
        self.assertEqual(call("scale", "0.000"), "3")
        self.assertEqual(call("scale", 100), "0")

    def test_min_scale_strips_trailing_zeros(self):
        self.assertEqual(call("min_scale", "8.4100"), "2")
        self.assertEqual(call("min_scale", "100.00"), "0")
        self.assertEqual(call("min_scale", "0.000"), "0")

    def test_trim_scale(self):
        self.assertEqual(call("trim_scale", "8.4100"), "8.41")
        self.assertEqual(call("trim_scale", "100.00"), "100")
        self.assertEqual(call("trim_scale", "0.000"), "0")


class NullAndErrorTests(unittest.TestCase):
    def test_null_propagation(self):
        self.assertIsNone(call("+", None, 3))
        self.assertIsNone(call("round", None))
        self.assertIsNone(call("gcd", None, 5))
        self.assertIsNone(call("width_bucket", None, 0, 10, 5))
        self.assertIsNone(call("factorial", None))

    def test_errors_are_function_errors(self):
        # These must raise a catchable FunctionError instead of crashing.
        for c in (("sqrt", -1), ("ln", 0), ("/", 1, 0),
                  ("factorial", -1), ("log", 1, 2)):
            self.assertTrue(fails(*c))

    def test_negative_zero_is_preserved(self):
        # trim_scale drops fractional zeros but keeps the negative sign.
        self.assertEqual(call("trim_scale", "-0.0"), "-0")
        self.assertEqual(call("abs", "-0.0"), "0.0")

    def test_numeric_overflow_is_reported(self):
        big = "9" * 600
        self.assertTrue(fails("*", big, big))


if __name__ == "__main__":
    unittest.main()
