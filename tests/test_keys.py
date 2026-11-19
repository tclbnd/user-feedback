import unittest

from mjoin.keys import describe, key_of, passes, sort_key
from mjoin.nestloop import nested_loop


class KeyTests(unittest.TestCase):
    def test_composite_sort_puts_nulls_last_per_column(self):
        rows = [{"a": "us", "b": 1}, {"a": "eu", "b": None}, {"a": "eu", "b": 7}]
        got = sorted(rows, key=lambda r: sort_key(key_of(r, ["a", "b"])))
        self.assertEqual([(r["a"], r["b"]) for r in got], [("eu", 7), ("eu", None), ("us", 1)])

    def test_filter_and_describe(self):
        self.assertTrue(passes([{"l": "d", "op": ">=", "r": "f"}], {"d": 5}, {"f": 4}))
        self.assertEqual(describe(["a", "b"], ["a", "b"], [{"l": "d", "op": ">=", "r": "f"}]),
                         "l.a = r.a AND l.b = r.b AND l.d >= r.f")

    def test_nested_loop_reference(self):
        left = [{"id": "x", "k": 1, "d": 2}]
        right = [{"id": "y", "k": 1, "f": 5}]
        self.assertEqual(nested_loop(left, right, "k", "k", "left", [{"l": "d", "op": ">=", "r": "f"}]), [("x", None)])


if __name__ == "__main__":
    unittest.main()
