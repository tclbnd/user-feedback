"""Behavior must match PostgreSQL for every input, not just the happy path.

Each expectation below corresponds to what PostgreSQL returns (or the error
it raises) for the same array and the same arguments.
"""

import unittest

from arr import core, text
from arrcall import call
from arr.core import ArrayError


class SubscriptTests(unittest.TestCase):
    def test_in_range(self):
        self.assertEqual(core.subscript(["a", "b"], 1), "a")
        self.assertEqual(core.subscript(["a", "b"], 2), "b")

    def test_out_of_range_is_null_not_crash(self):
        # Reported: a[3] on a two-element array crashed the engine;
        # PostgreSQL returns NULL.
        self.assertIsNone(core.subscript(["a", "b"], 3))
        self.assertIsNone(core.subscript(["a", "b"], 0))
        self.assertIsNone(core.subscript(["a", "b"], -1))
        self.assertIsNone(core.subscript([], 1))

    def test_null_array_and_null_subscript(self):
        self.assertIsNone(core.subscript(None, 1))
        self.assertIsNone(core.subscript(["a"], None))
        self.assertIsNone(core.subscript(None, None))


class SliceTests(unittest.TestCase):
    def test_bounds_are_clamped(self):
        self.assertEqual(core.slice_([1, 2, 3], 2, 9), [2, 3])
        self.assertEqual(core.slice_([1, 2, 3], 0, 2), [1, 2])
        self.assertEqual(core.slice_([1, 2, 3], -5, 2), [1, 2])

    def test_empty_ranges(self):
        self.assertEqual(core.slice_([1, 2, 3], 3, 2), [])
        self.assertEqual(core.slice_([1, 2, 3], 5, 9), [])
        self.assertEqual(core.slice_([], 1, 2), [])

    def test_null_array_or_bounds(self):
        self.assertIsNone(core.slice_(None, 1, 2))
        self.assertIsNone(core.slice_([1, 2, 3], None, 2))
        self.assertIsNone(core.slice_([1, 2, 3], 1, None))


class LengthTests(unittest.TestCase):
    def test_empty_array_has_no_dimensions(self):
        # Reported: array_length('{}') returned 0; PostgreSQL returns NULL.
        self.assertIsNone(core.array_length([], 1))
        self.assertIsNone(core.array_lower([], 1))
        self.assertIsNone(core.array_upper([], 1))
        # cardinality is the one function that reports 0 for an empty array.
        self.assertEqual(core.cardinality([]), 0)

    def test_nonempty(self):
        self.assertEqual(core.array_length([1, 2], 1), 2)
        self.assertEqual(core.array_lower([1, 2], 1), 1)
        self.assertEqual(core.array_upper([1, 2], 1), 2)

    def test_null_array_and_other_dimensions(self):
        self.assertIsNone(core.array_length(None, 1))
        self.assertIsNone(core.array_length([1, 2], 2))
        self.assertIsNone(core.array_length([1, 2], 0))
        self.assertIsNone(core.array_length([1, 2], None))
        self.assertIsNone(core.cardinality(None))


class AppendCatTests(unittest.TestCase):
    def test_append_and_prepend(self):
        self.assertEqual(core.array_append([1, 2], 3), [1, 2, 3])
        self.assertEqual(core.array_append([], 1), [1])
        self.assertEqual(core.array_prepend(0, [1, 2]), [0, 1, 2])

    def test_null_element_is_a_real_element(self):
        self.assertEqual(core.array_append([1], None), [1, None])
        self.assertEqual(core.array_prepend(None, [1]), [None, 1])

    def test_null_array(self):
        self.assertIsNone(core.array_append(None, 1))
        self.assertIsNone(core.array_prepend(1, None))

    def test_cat_treats_null_as_empty(self):
        self.assertIsNone(core.array_cat(None, None))
        self.assertEqual(core.array_cat(None, [1]), [1])
        self.assertEqual(core.array_cat([1], None), [1])
        self.assertEqual(core.array_cat([1], [2]), [1, 2])
        self.assertEqual(core.array_cat([], []), [])


class RemoveReplaceTests(unittest.TestCase):
    def test_remove(self):
        self.assertEqual(core.array_remove([1, None, 2, 1], 1), [None, 2])
        self.assertEqual(core.array_remove([1, None], None), [1])
        self.assertEqual(core.array_remove([1, 2], 9), [1, 2])
        self.assertIsNone(core.array_remove(None, 1))

    def test_replace(self):
        self.assertEqual(core.array_replace([1, None, 2], None, 0), [1, 0, 2])
        self.assertEqual(core.array_replace([1, 2], 1, None), [None, 2])
        self.assertEqual(core.array_replace([1, 2], 9, 0), [1, 2])
        self.assertIsNone(core.array_replace(None, 1, 2))


class PositionTests(unittest.TestCase):
    def test_position(self):
        self.assertEqual(core.array_position([1, None, 3], None), 2)
        self.assertEqual(core.array_position([1, 2, 3], 2, 2), 2)
        self.assertIsNone(core.array_position([1, 2, 3], 2, 3))
        self.assertIsNone(core.array_position([1, 2], 9))
        self.assertIsNone(core.array_position(None, 1))
        # The searched value may be NULL; a NULL start means "not specified"
        # and the search begins at the first element.
        self.assertEqual(core.array_position([1, 2], 2, None), 2)
        self.assertEqual(core.array_position([1, 2, 1], 1, 2), 3)

    def test_negative_start_is_an_error(self):
        with self.assertRaises(ArrayError):
            core.array_position([1, 2], 1, 0)

    def test_positions(self):
        self.assertEqual(core.array_positions([1, None, 1], 1), [1, 3])
        self.assertEqual(core.array_positions([None, None], None), [1, 2])
        self.assertEqual(core.array_positions([1, 2], 9), [])
        self.assertIsNone(core.array_positions(None, 1))


class ContainsOverlapsTests(unittest.TestCase):
    def test_contains_ignores_duplicates(self):
        self.assertTrue(core.contains([1, 4, 3], [3, 1, 3]))
        self.assertTrue(core.contains([1], [1, 1]))
        self.assertFalse(core.contains([1, 2], [3]))

    def test_contains_null_elements(self):
        self.assertTrue(core.contains([None], [None]))
        self.assertFalse(core.contains([1], [None]))
        self.assertFalse(core.contains([None], [1]))

    def test_contains_empty(self):
        self.assertTrue(core.contains([], []))
        self.assertFalse(core.contains([], [1]))
        self.assertTrue(core.contains([1], []))

    def test_overlaps(self):
        self.assertTrue(core.overlaps([1, 4, 3], [2, 1]))
        self.assertFalse(core.overlaps([1], [2]))
        self.assertTrue(core.overlaps([None], [None]))
        self.assertFalse(core.overlaps([], []))
        self.assertFalse(core.overlaps([], [1]))

    def test_null_arrays(self):
        self.assertIsNone(core.contains(None, [1]))
        self.assertIsNone(core.contains([1], None))
        self.assertIsNone(core.overlaps(None, [1]))
        self.assertIsNone(core.overlaps([1], None))


class TrimTests(unittest.TestCase):
    def test_trim(self):
        self.assertEqual(core.trim_array([1, 2, 3], 1), [1, 2])
        self.assertEqual(core.trim_array([1, 2, 3], 0), [1, 2, 3])
        self.assertEqual(core.trim_array([1, 2, 3], 3), [])
        self.assertEqual(core.trim_array([], 0), [])

    def test_errors(self):
        with self.assertRaises(ArrayError):
            core.trim_array([1], 2)
        with self.assertRaises(ArrayError):
            core.trim_array([1], -1)
        with self.assertRaises(ArrayError):
            core.trim_array([], 1)

    def test_null(self):
        self.assertIsNone(core.trim_array(None, 1))
        self.assertIsNone(core.trim_array([1], None))


class ArrayToStringTests(unittest.TestCase):
    def test_null_elements_are_omitted(self):
        # Reported: NULL elements were written as '' producing 'red,,blue'.
        self.assertEqual(text.array_to_string(["red", None, "blue"], ","), "red,blue")
        self.assertEqual(text.array_to_string([None, None], ","), "")
        self.assertEqual(text.array_to_string([], ","), "")

    def test_null_string_replaces_nulls(self):
        self.assertEqual(text.array_to_string(["red", None, "blue"], ",", "-"), "red,-,blue")
        self.assertEqual(text.array_to_string([1, None, 2], ",", ""), "1,,2")
        # A NULL null_string behaves like no null_string.
        self.assertEqual(text.array_to_string(["red", None], ",", None), "red")

    def test_null_inputs(self):
        self.assertIsNone(text.array_to_string(None, ","))
        self.assertIsNone(text.array_to_string(["a"], None))

    def test_element_output(self):
        self.assertEqual(text.array_to_string([1, 2], ","), "1,2")
        self.assertEqual(text.array_to_string([True, False], ","), "t,f")


class StringToArrayTests(unittest.TestCase):
    def test_empty_fields_stay_empty_strings(self):
        self.assertEqual(text.string_to_array("red,,blue", ","), ["red", "", "blue"])

    def test_empty_input_is_empty_array(self):
        self.assertEqual(text.string_to_array("", ","), [])
        self.assertEqual(text.string_to_array("", ",", "x"), [])
        self.assertEqual(text.string_to_array("", None), [])

    def test_null_string_becomes_null(self):
        self.assertEqual(text.string_to_array("a,b,,c", ",", ""), ["a", "b", None, "c"])
        self.assertEqual(text.string_to_array("xx~~yy~~zz", "~~", "yy"), ["xx", None, "zz"])

    def test_delimiters(self):
        self.assertEqual(text.string_to_array("abc", None), ["a", "b", "c"])
        self.assertEqual(text.string_to_array("abc", ""), ["abc"])
        self.assertEqual(text.string_to_array("abc", ","), ["abc"])

    def test_null_input(self):
        self.assertIsNone(text.string_to_array(None, ","))


class ArrayOutTests(unittest.TestCase):
    def test_whitespace_is_quoted(self):
        # Reported: tags with spaces were not quoted, so downstream parsers
        # mis-split them.
        self.assertEqual(text.array_out(["big sale", None, "x"]), '{"big sale",NULL,x}')
        self.assertEqual(text.array_out(["a\tb"]), '{"a\tb"}')

    def test_empty_and_null_looking_strings_are_quoted(self):
        self.assertEqual(text.array_out([""]), '{""}')
        self.assertEqual(text.array_out(["NULL"]), '{"NULL"}')
        self.assertEqual(text.array_out(["null"]), '{"null"}')

    def test_special_characters_are_escaped(self):
        self.assertEqual(text.array_out(["a,b"]), '{"a,b"}')
        self.assertEqual(text.array_out(["a{b"]), '{"a{b"}')
        self.assertEqual(text.array_out(['a"b']), '{"a\\"b"}')
        self.assertEqual(text.array_out(["a\\b"]), '{"a\\\\b"}')

    def test_plain_elements_and_nulls(self):
        self.assertEqual(text.array_out(["red", "blue"]), "{red,blue}")
        self.assertEqual(text.array_out([1, 2]), "{1,2}")
        self.assertEqual(text.array_out([None]), "{NULL}")
        self.assertEqual(text.array_out([True]), "{t}")
        self.assertEqual(text.array_out([]), "{}")
        self.assertIsNone(text.array_out(None))


class CallWrapperTests(unittest.TestCase):
    def test_errors_are_reported_not_raised(self):
        self.assertIn("error", call(["trim_array", [1], 2]))
        self.assertIn("value", call(["subscript", ["a", "b"], 3]))


if __name__ == "__main__":
    unittest.main()
