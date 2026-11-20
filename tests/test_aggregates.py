import unittest

from aggfn.aggregates import AGGREGATES as A, AggregateError


class AggregateTests(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(A["count_star"]("int4", [1, 2]), "2")
        self.assertEqual(A["sum"]("int4", [1, 2, 3]), "6")
        self.assertEqual(A["sum"]("numeric", ["1.10", "2"]), "3.10")
        self.assertEqual(A["min"]("int4", [3, 1, 2]), "1")
        self.assertEqual(A["bool_or"]("bool", [False, True]), "t")

    def test_count(self):
        # count(x) ignores NULLs, count(*) does not
        self.assertEqual(A["count"]("int4", [1042, None, 1043]), "2")
        self.assertEqual(A["count_star"]("int4", [1042, None, 1043]), "3")
        self.assertEqual(A["count"]("int4", [None, None]), "0")
        self.assertEqual(A["count"]("int4", []), "0")
        self.assertEqual(A["count_star"]("int4", []), "0")
        # count(DISTINCT x) ignores NULLs and compares numerics by value
        self.assertEqual(A["count_distinct"]("int4", [1, 2, 2, None]), "2")
        self.assertEqual(A["count_distinct"]("numeric", ["1.0", "1.00", "2"]), "2")
        self.assertEqual(A["count_distinct"]("text", ["a", "a", None]), "1")

    def test_sum(self):
        self.assertIsNone(A["sum"]("numeric", []))       # empty group -> NULL
        self.assertIsNone(A["sum"]("int4", [None]))
        self.assertEqual(A["sum"]("int4", [1, None, 2]), "3")
        self.assertEqual(A["sum"]("int8", [1, 2]), "3")  # int8 sum -> numeric
        self.assertEqual(A["sum"]("numeric", ["0.10", "0.20"]), "0.30")
        self.assertEqual(A["sum"]("numeric", ["-1.5", "1.5"]), "0.0")
        with self.assertRaises(AggregateError):          # int8 overflow
            A["sum"]("int4", [2 ** 62, 2 ** 62])

    def test_avg(self):
        # avg returns numeric, scale follows select_div_scale()
        self.assertEqual(A["avg"]("int4", [1, 2, 4]), "2.3333333333333333")
        self.assertEqual(A["avg"]("int4", [1, 2]), "1.5000000000000000")
        self.assertEqual(A["avg"]("int4", [5]), "5.0000000000000000")
        self.assertEqual(A["avg"]("int4", [0]), "0.00000000000000000000")
        self.assertEqual(A["avg"]("int8", [10, 20]), "15.0000000000000000")
        self.assertEqual(A["avg"]("numeric", ["1.50", "2.50"]), "2.0000000000000000")
        self.assertEqual(A["avg"]("numeric", ["0.1"]), "0.10000000000000000000")
        self.assertEqual(A["avg"]("int4", [-1, -2]), "-1.5000000000000000")
        self.assertIsNone(A["avg"]("int4", []))

    def test_min_max(self):
        self.assertEqual(A["max"]("numeric", ["19.90", None, "7.50"]), "19.90")
        self.assertEqual(A["min"]("numeric", ["19.90", None, "7.50"]), "7.50")
        self.assertEqual(A["max"]("int4", [3, None, 1]), "3")
        self.assertEqual(A["min"]("text", ["b", "a", None]), "a")
        self.assertEqual(A["max"]("bool", [False, True]), "t")
        self.assertIsNone(A["max"]("int4", []))
        self.assertIsNone(A["min"]("numeric", [None]))

    def test_string_agg(self):
        self.assertEqual(A["string_agg"]("text", ["red", None, "blue"], ", "), "red, blue")
        self.assertIsNone(A["string_agg"]("text", [None], ", "))
        self.assertIsNone(A["string_agg"]("text", [], ", "))
        self.assertEqual(A["string_agg"]("text", ["a"], "-"), "a")

    def test_array_agg(self):
        # NULLs are included; quoting follows array_out
        self.assertEqual(A["array_agg"]("text", ["gift wrap", None, ""]),
                         '{"gift wrap",NULL,""}')
        self.assertEqual(A["array_agg"]("int4", [1, None, 2]), "{1,NULL,2}")
        self.assertEqual(A["array_agg"]("bool", [True, False]), "{t,f}")
        self.assertEqual(A["array_agg"]("numeric", ["1.50", "2"]), "{1.50,2}")
        self.assertEqual(A["array_agg"]("text", ["null", "a,b", 'a"b', "a\\b"]),
                         '{"null","a,b","a\\"b","a\\\\b"}')
        self.assertIsNone(A["array_agg"]("int4", []))

    def test_json_agg(self):
        self.assertEqual(A["json_agg"]("int4", [1, None, 2]), "[1, null, 2]")
        self.assertEqual(A["json_agg"]("numeric", ["1.50"]), "[1.50]")
        self.assertEqual(A["json_agg"]("bool", [True, None]), "[true, null]")
        self.assertEqual(A["json_agg"]("text", ["a", "b"]), '["a", "b"]')
        self.assertEqual(A["json_agg"]("text", ['a"b', "é\n"]), '["a\\"b", "é\\n"]')
        self.assertIsNone(A["json_agg"]("int4", []))

    def test_bool_aggs(self):
        self.assertEqual(A["bool_and"]("bool", [True, None, True]), "t")
        self.assertEqual(A["bool_and"]("bool", [True, False, None]), "f")
        self.assertEqual(A["bool_or"]("bool", [False, None]), "f")
        self.assertEqual(A["every"]("bool", [True, True]), "t")
        self.assertIsNone(A["bool_and"]("bool", []))
        self.assertIsNone(A["bool_or"]("bool", [None]))

    def test_stddev_var(self):
        # numeric results with select_div_scale/sqrt_var display scales
        self.assertEqual(A["var_samp"]("int4", [1, 2, 3]), "1.00000000000000000000")
        self.assertEqual(A["var_samp"]("int4", [1, 2]), "0.50000000000000000000")
        self.assertEqual(A["stddev_samp"]("int4", [1, 2]), "0.70710678118654752440")
        self.assertEqual(A["stddev_samp"]("int4", list(range(1, 11))),
                         "3.0276503540974917")
        self.assertEqual(A["stddev_samp"]("int4", [5, 5, 5]), "0.0000000000000000")
        self.assertEqual(A["var_samp"]("numeric", ["1.5", "2.5"]),
                         "0.50000000000000000000")
        self.assertIsNone(A["stddev_samp"]("int4", [5]))   # N < 2 -> NULL
        self.assertIsNone(A["var_samp"]("int4", []))
        self.assertIsNone(A["stddev_samp"]("int4", [None, None]))

    def test_percentile_cont(self):
        self.assertEqual(A["percentile_cont"]("int4", [10, 20, 30, 40], 0.5), "25")
        self.assertEqual(A["percentile_cont"]("int4", [1, 2], 0.3), "1.3")
        self.assertEqual(A["percentile_cont"]("int4", [1, 2], 0.5), "1.5")
        self.assertEqual(A["percentile_cont"]("int4", [5], 0.7), "5")
        self.assertEqual(A["percentile_cont"]("int4", [40, 10, 30, 20], 1), "40")
        self.assertEqual(A["percentile_cont"]("int4", [40, 10, 30, 20], 0), "10")
        # NULLs ignored, result is float8 text
        self.assertEqual(A["percentile_cont"]("int4", [10, None, 20], 0.5), "15")
        self.assertEqual(A["percentile_cont"]("int8", [10 ** 15], 0.5), "1e+15")
        self.assertEqual(A["percentile_cont"]("numeric", ["1.5", "2.5"], 0.5), "2")
        self.assertIsNone(A["percentile_cont"]("int4", [], 0.5))
        self.assertIsNone(A["percentile_cont"]("int4", [None], 0.5))
        for bad in (-0.1, 1.5, 2):
            with self.assertRaises(AggregateError) as ctx:
                A["percentile_cont"]("int4", [1, 2], bad)
            self.assertIn("is not between 0 and 1", str(ctx.exception))

    def test_percentile_disc(self):
        self.assertEqual(A["percentile_disc"]("int4", [1, 2, 3, 4], 0.5), "2")
        self.assertEqual(A["percentile_disc"]("int4", [1, 2, 3, 4], 0.25), "1")
        self.assertEqual(A["percentile_disc"]("int4", [1, 2, 3, 4], 0), "1")
        self.assertEqual(A["percentile_disc"]("int4", [1, 2, 3, 4], 1), "4")
        self.assertEqual(A["percentile_disc"]("int4", [4, 1, 3, None, 2], 0.75), "3")
        # returns the input datum, keeping its type and scale
        self.assertEqual(A["percentile_disc"]("numeric", ["1.50", "2.25"], 0.9), "2.25")
        self.assertEqual(A["percentile_disc"]("text", ["b", "a"], 0.5), "a")
        self.assertIsNone(A["percentile_disc"]("int4", [], 0.5))
        with self.assertRaises(AggregateError):
            A["percentile_disc"]("int4", [1, 2], 1.5)

    def test_mode(self):
        self.assertEqual(A["mode"]("int4", [1, 2, 2, 3]), "2")
        # ties: first value in sort order wins
        self.assertEqual(A["mode"]("int4", [1, 2, 2, 3, 3]), "2")
        self.assertEqual(A["mode"]("text", ["b", "a", "a", "b"]), "a")
        self.assertEqual(A["mode"]("numeric", ["1.50", "1.50", "2"]), "1.50")
        self.assertEqual(A["mode"]("numeric", ["1.5", "2.5", "2.5"]), "2.5")
        self.assertIsNone(A["mode"]("int4", []))
        self.assertIsNone(A["mode"]("int4", [None]))


if __name__ == "__main__":
    unittest.main()
