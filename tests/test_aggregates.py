import unittest

from aggfn.aggregates import AGGREGATES as A


class AggregateTests(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(A["count_star"]("int4", [1, 2]), "2")
        self.assertEqual(A["sum"]("int4", [1, 2, 3]), "6")
        self.assertEqual(A["sum"]("numeric", ["1.10", "2"]), "3.10")
        self.assertEqual(A["min"]("int4", [3, 1, 2]), "1")
        self.assertEqual(A["bool_or"]("bool", [False, True]), "t")


if __name__ == "__main__":
    unittest.main()
