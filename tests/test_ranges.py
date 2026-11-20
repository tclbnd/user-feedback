import unittest

from rangecall import call


def value(c):
    return call(c)["value"]


def error(c):
    return call(c)["error"]


class RangeTests(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(value(["int4range", "[1,5)"]), "[1,5)")
        self.assertEqual(value(["lower", ["int4range", "[1,5)"]]), "1")
        self.assertEqual(value(["&&", ["int4range", "[1,5)"], ["int4range", "[3,8)"]]), "t")
        self.assertEqual(value(["isempty", ["numrange", "empty"]]), "t")
        self.assertEqual(value(["upper_inf", ["int4range", "[1,)"]]), "t")

    def test_canonical_form_discrete(self):
        # int4range / daterange normalize to [)
        self.assertEqual(value(["int4range", "[101,105]"]), "[101,106)")
        self.assertEqual(value(["upper", ["int4range", "[101,105]"]]), "106")
        self.assertEqual(value(["int4range", "(1,5]"]), "[2,6)")
        self.assertEqual(value(["int4range", "(1,5)"]), "[2,5)")
        self.assertEqual(value(["daterange", "[2026-11-20,2026-11-23]"]), "[2026-11-20,2026-11-24)")
        # numrange keeps its bounds
        self.assertEqual(value(["numrange", "[1,5]"]), "[1,5]")
        self.assertEqual(value(["numrange", "(1.5,2.50]"]), "(1.5,2.50]")

    def test_empty(self):
        self.assertEqual(value(["daterange", "(2026-11-20,2026-11-21)"]), "empty")
        self.assertEqual(value(["int4range", "[5,5)"]), "empty")
        self.assertEqual(value(["int4range", "(5,5]"]), "empty")
        self.assertEqual(value(["numrange", "(1,1)"]), "empty")
        self.assertEqual(value(["numrange", "[1,1]"]), "[1,1]")
        self.assertEqual(value(["int4range", " EMPTY "]), "empty")
        self.assertIsNone(value(["lower", ["int4range", "empty"]]))
        self.assertIsNone(value(["upper", ["int4range", "empty"]]))
        self.assertEqual(value(["isempty", ["int4range", "empty"]]), "t")
        self.assertEqual(value(["lower_inc", ["int4range", "empty"]]), "f")
        self.assertEqual(value(["upper_inc", ["int4range", "empty"]]), "f")
        self.assertEqual(value(["lower_inf", ["int4range", "empty"]]), "f")
        self.assertEqual(value(["upper_inf", ["int4range", "empty"]]), "f")

    def test_unbounded(self):
        self.assertEqual(value(["int4range", "[,]"]), "(,)")
        self.assertEqual(value(["numrange", "(,5]"]), "(,5]")
        self.assertEqual(value(["lower_inf", ["daterange", "(,)"]]), "t")
        self.assertEqual(value(["upper_inf", ["daterange", "(2026-11-20,)"]]), "t")
        self.assertIsNone(value(["lower", ["daterange", "(,)"]]))
        self.assertEqual(value(["lower_inc", ["numrange", "(,5]"]]), "f")
        self.assertEqual(value(["upper_inc", ["numrange", "(,5]"]]), "t")

    def test_contains(self):
        self.assertEqual(value(["@>", ["daterange", "[2026-11-20,2026-11-23)"], "2026-11-23"]), "f")
        self.assertEqual(value(["@>", ["daterange", "[2026-11-20,2026-11-23)"], "2026-11-22"]), "t")
        self.assertEqual(value(["<@", "2026-11-20", ["daterange", "[2026-11-20,2026-11-23)"]]), "t")
        self.assertEqual(value(["@>", ["numrange", "[1,5]"], ["numrange", "[1,5)"]]), "t")
        self.assertEqual(value(["@>", ["numrange", "[1,5)"], ["numrange", "[1,5]"]]), "f")
        self.assertEqual(value(["@>", ["numrange", "empty"], ["numrange", "empty"]]), "t")
        self.assertEqual(value(["<@", ["numrange", "empty"], ["numrange", "[1,5]"]]), "t")
        self.assertEqual(value(["@>", ["numrange", "empty"], ["numrange", "[1,5]"]]), "f")
        self.assertEqual(value(["@>", ["numrange", "empty"], "3"]), "f")

    def test_overlaps_adjacent(self):
        self.assertEqual(value(["&&", ["numrange", "[1,3)"], ["numrange", "[3,5)"]]), "f")
        self.assertEqual(value(["&&", ["numrange", "[1,3]"], ["numrange", "[3,5)"]]), "t")
        self.assertEqual(value(["&&", ["numrange", "empty"], ["numrange", "[1,5)"]]), "f")
        self.assertEqual(value(["-|-", ["int4range", "[1,5)"], ["int4range", "[5,8)"]]), "t")
        self.assertEqual(value(["-|-", ["numrange", "[1,5)"], ["numrange", "[5,8)"]]), "t")
        self.assertEqual(value(["-|-", ["numrange", "[1,5]"], ["numrange", "[5,8)"]]), "f")
        self.assertEqual(value(["-|-", ["numrange", "[1,5)"], ["numrange", "(5,8)"]]), "f")
        self.assertEqual(value(["-|-", ["numrange", "empty"], ["numrange", "[1,5)"]]), "f")

    def test_union(self):
        self.assertEqual(value(["+", ["int4range", "[1,5)"], ["int4range", "[5,8)"]]), "[1,8)")
        self.assertEqual(value(["+", ["numrange", "[1,5]"], ["numrange", "(5,8)"]]), "[1,8)")
        self.assertEqual(value(["+", ["int4range", "empty"], ["int4range", "[5,8)"]]), "[5,8)")
        self.assertEqual(value(["+", ["numrange", "(,5)"], ["numrange", "[5,)"]]), "(,)")
        self.assertIn("result of range union would not be contiguous",
                      error(["+", ["int4range", "[101,103)"], ["int4range", "[106,110)"]]))
        self.assertIn("result of range union would not be contiguous",
                      error(["+", ["numrange", "[1,2]"], ["numrange", "[3,4]"]]))

    def test_intersection(self):
        self.assertEqual(value(["*", ["numrange", "[100,200)"], ["numrange", "[150,300)"]]), "[150,200)")
        self.assertEqual(value(["*", ["int4range", "[1,5)"], ["int4range", "[5,8)"]]), "empty")
        self.assertEqual(value(["*", ["numrange", "[1,5]"], ["numrange", "[5,8]"]]), "[5,5]")
        self.assertEqual(value(["*", ["numrange", "(,)"], ["numrange", "[1,2)"]]), "[1,2)")
        self.assertEqual(value(["*", ["numrange", "empty"], ["numrange", "[1,2)"]]), "empty")

    def test_difference(self):
        self.assertEqual(value(["-", ["int4range", "[1,10)"], ["int4range", "[1,3)"]]), "[3,10)")
        self.assertEqual(value(["-", ["int4range", "[1,10)"], ["int4range", "[8,20)"]]), "[1,8)")
        self.assertEqual(value(["-", ["int4range", "[1,10)"], ["int4range", "[20,30)"]]), "[1,10)")
        self.assertEqual(value(["-", ["int4range", "[1,10)"], ["int4range", "[1,10)"]]), "empty")
        self.assertEqual(value(["-", ["numrange", "[1,5]"], ["numrange", "(1,5]"]]), "[1,1]")
        self.assertIn("result of range difference would not be contiguous",
                      error(["-", ["numrange", "[1,5]"], ["numrange", "[2,3]"]]))
        self.assertIn("result of range difference would not be contiguous",
                      error(["-", ["int4range", "[1,10)"], ["int4range", "[3,5)"]]))

    def test_range_merge(self):
        self.assertEqual(value(["range_merge", ["int4range", "[101,103)"], ["int4range", "[106,110)"]]),
                         "[101,110)")
        self.assertEqual(value(["range_merge", ["numrange", "empty"], ["numrange", "[1,2)"]]), "[1,2)")
        self.assertEqual(value(["range_merge", ["numrange", "empty"], ["numrange", "empty"]]), "empty")
        self.assertEqual(value(["range_merge", ["numrange", "[5,)"], ["numrange", "(,1)"]]), "(,)")

    def test_ordering(self):
        self.assertEqual(value(["<", ["int4range", "empty"], ["int4range", "[1,2)"]]), "t")
        self.assertEqual(value(["<", ["int4range", "[1,2)"], ["int4range", "empty"]]), "f")
        self.assertEqual(value(["<", ["numrange", "(,5)"], ["numrange", "[1,2)"]]), "t")
        self.assertEqual(value(["<", ["numrange", "[1,5)"], ["numrange", "(1,5)"]]), "t")
        self.assertEqual(value(["<", ["numrange", "[1,5)"], ["numrange", "[1,5]"]]), "t")
        self.assertEqual(value(["<", ["int4range", "[1,5)"], ["int4range", "[1,6)"]]), "t")

    def test_input_errors(self):
        self.assertIn("range lower bound must be less than or equal to range upper bound",
                      error(["int4range", "[5,1)"]))
        self.assertIn("range lower bound must be less than or equal to range upper bound",
                      error(["numrange", "[5,1]"]))
        self.assertIn("malformed range literal", error(["int4range", "1,5"]))
        self.assertIn("malformed range literal", error(["int4range", "[1,2,3]"]))
        self.assertIn("malformed range literal", error(["int4range", "[1,2"]))
        self.assertIn("invalid input syntax for type integer", error(["int4range", "[1.5,3)"]))
        self.assertIn("invalid input syntax for type numeric", error(["numrange", "[a,b]"]))
        self.assertIn("invalid input syntax for type date", error(["daterange", "[not-a-date,)"]))
        self.assertIn("out of range for type integer", error(["int4range", "[1,2147483648)"]))
        self.assertIn("integer out of range", error(["int4range", "[1,2147483647]"]))
        self.assertIn("integer out of range", error(["int4range", "(2147483647,)"]))
        self.assertEqual(value(["int4range", "(2147483646,2147483647)"]), "empty")

    def test_nested_and_null(self):
        self.assertEqual(value(["upper", ["int4range", "[101,105]"]]), "106")
        self.assertEqual(value(["@>", ["int4range", "[1,10)"], ["upper", ["int4range", "[5,8]"]]]), "t")
        self.assertIsNone(value(["<@", ["upper", ["int4range", "empty"]], ["int4range", "[1,10)"]]))


if __name__ == "__main__":
    unittest.main()
