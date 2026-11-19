"""Behavioural tests mirroring PostgreSQL jsonb semantics."""
import unittest

from jcall import call


def value(c):
    r = call(c)
    assert "error" not in r, r
    return r["value"]


def error(c):
    r = call(c)
    assert "error" in r, r
    return r["error"]


class ConcatTests(unittest.TestCase):
    def test_arrays(self):
        self.assertEqual(value(["||", "[1, 2]", "[3]"]), "[1, 2, 3]")

    def test_array_scalar(self):
        self.assertEqual(value(["||", "[1]", "2"]), "[1, 2]")
        self.assertEqual(value(["||", "2", "[1]"]), "[2, 1]")

    def test_array_object(self):
        self.assertEqual(value(["||", "[1]", '{"a": 1}']), '[1, {"a": 1}]')
        self.assertEqual(value(["||", '{"a": 1}', "[1]"]), '[{"a": 1}, 1]')

    def test_objects_merge(self):
        self.assertEqual(value(["||", '{"a": 1, "b": 1}', '{"b": 2, "c": 3}']),
                         '{"a": 1, "b": 2, "c": 3}')

    def test_scalar_scalar_errors(self):
        self.assertEqual(error(["||", "1", "2"]),
                         "invalid concatenation of jsonb objects")
        self.assertEqual(error(["||", "null", "null"]),
                         "invalid concatenation of jsonb objects")
        self.assertEqual(error(["||", '{"a": 1}', "2"]),
                         "invalid concatenation of jsonb objects")

    def test_json_null_is_scalar(self):
        self.assertEqual(value(["||", "null", "[1]"]), "[null, 1]")
        self.assertEqual(value(["||", "[1]", "null"]), "[1, null]")

    def test_sql_null(self):
        self.assertIsNone(value(["||", None, "[1]"]))
        self.assertIsNone(value(["||", "[1]", None]))


class DeleteTests(unittest.TestCase):
    def test_negative_index(self):
        self.assertEqual(value(["-", "[1, 2, 3]", -1]), "[1, 2]")
        self.assertEqual(value(["-", "[1, 2, 3]", -3]), "[2, 3]")

    def test_out_of_range_index_is_noop(self):
        self.assertEqual(value(["-", "[1, 2, 3]", -4]), "[1, 2, 3]")
        self.assertEqual(value(["-", "[1, 2, 3]", 3]), "[1, 2, 3]")

    def test_object_with_integer_errors(self):
        self.assertEqual(error(["-", '{"a": 1}', 0]),
                         "cannot delete from object using integer")

    def test_scalar_errors(self):
        self.assertEqual(error(["-", '"s"', "a"]), "cannot delete from scalar")
        self.assertEqual(error(["-", '"s"', 0]), "cannot delete from scalar")
        self.assertEqual(error(["-", "null", "a"]), "cannot delete from scalar")

    def test_string_elements(self):
        self.assertEqual(value(["-", '["a", 1, "a"]', "a"]), "[1]")

    def test_key_array(self):
        self.assertEqual(value(["-", '{"a": 1, "b": 2}', ["a", "x"]]), '{"b": 2}')
        self.assertEqual(value(["-", '["a", "b"]', ["a"]]), '["b"]')
        self.assertEqual(value(["-", '{"a": 1}', [None]]), '{"a": 1}')
        self.assertEqual(error(["-", '"s"', ["a"]]), "cannot delete from scalar")

    def test_sql_null(self):
        self.assertIsNone(value(["-", '{"a": 1}', None]))
        self.assertIsNone(value(["-", None, "a"]))


class DeletePathTests(unittest.TestCase):
    def test_negative_index(self):
        self.assertEqual(value(["#-", "[1, 2, 3]", ["-1"]]), "[1, 2]")

    def test_nested(self):
        self.assertEqual(value(["#-", '{"a": {"b": 1}}', ["a", "b"]]), '{"a": {}}')

    def test_missing_is_noop(self):
        self.assertEqual(value(["#-", '{"a": 1}', ["x"]]), '{"a": 1}')
        self.assertEqual(value(["#-", '{"a": 1}', ["a", "b"]]), '{"a": 1}')
        self.assertEqual(value(["#-", "[1, 2]", ["5"]]), "[1, 2]")
        self.assertEqual(value(["#-", "[1, 2]", ["-5"]]), "[1, 2]")
        self.assertEqual(value(["#-", '"s"', ["a"]]), '"s"')

    def test_empty_path(self):
        self.assertEqual(value(["#-", '{"a": 1}', []]), '{"a": 1}')

    def test_bad_index_errors(self):
        self.assertEqual(error(["#-", "[1, 2]", ["a"]]),
                         'path element at position 1 is not an integer: "a"')

    def test_null_path(self):
        self.assertIsNone(value(["#-", '{"a": 1}', None]))
        self.assertIsNone(value(["#-", '{"a": 1}', [None]]))


class SetTests(unittest.TestCase):
    def test_negative_index(self):
        self.assertEqual(value(["jsonb_set", "[1, 2, 3]", ["-1"], "9"]), "[1, 2, 9]")

    def test_out_of_range(self):
        self.assertEqual(value(["jsonb_set", "[1, 2]", ["5"], "9"]), "[1, 2, 9]")
        self.assertEqual(value(["jsonb_set", "[1, 2]", ["-5"], "9"]), "[9, 1, 2]")
        self.assertEqual(value(["jsonb_set", "[1, 2]", ["5"], "9", False]), "[1, 2]")
        self.assertEqual(value(["jsonb_set", "[1, 2]", ["-5"], "9", False]), "[1, 2]")

    def test_missing_intermediate_unchanged(self):
        self.assertEqual(value(["jsonb_set", "{}", ["a", "b"], "1"]), "{}")
        self.assertEqual(value(["jsonb_set", "{}", ["a"], "1"]), '{"a": 1}')
        self.assertEqual(value(["jsonb_set", "{}", ["a"], "1", False]), "{}")

    def test_scalar_in_path_errors(self):
        self.assertEqual(error(["jsonb_set", '{"a": 1}', ["a", "b"], "2"]),
                         "cannot set path in scalar")
        self.assertEqual(error(["jsonb_set", "1", ["a"], "2"]),
                         "cannot set path in scalar")

    def test_empty_path(self):
        self.assertEqual(value(["jsonb_set", "1", [], "2"]), "2")
        self.assertEqual(value(["jsonb_set", '{"a": 1}', [], "2"]), '{"a": 1}')

    def test_json_null_new_value(self):
        self.assertEqual(value(["jsonb_set", "{}", ["a"], "null"]), '{"a": null}')

    def test_sql_null(self):
        self.assertIsNone(value(["jsonb_set", None, ["a"], "1"]))
        self.assertIsNone(value(["jsonb_set", "{}", None, "1"]))
        self.assertIsNone(value(["jsonb_set", "{}", ["a"], None]))
        self.assertIsNone(value(["jsonb_set", "{}", ["a", None], "1"]))
        self.assertIsNone(value(["jsonb_set", "{}", ["a"], "1", None]))

    def test_bad_index_errors(self):
        self.assertEqual(error(["jsonb_set", "[1]", ["x"], "2"]),
                         'path element at position 1 is not an integer: "x"')

    def test_nested(self):
        self.assertEqual(value(["jsonb_set", '{"a": {"b": 1}}', ["a", "b"], "2"]),
                         '{"a": {"b": 2}}')
        self.assertEqual(value(["jsonb_set", '{"a": [1, 2]}', ["a", "-1"], "9"]),
                         '{"a": [1, 9]}')


class InsertTests(unittest.TestCase):
    def test_before_after(self):
        self.assertEqual(value(["jsonb_insert", '{"a": [0, 1, 2]}', ["a", "1"], '"x"']),
                         '{"a": [0, "x", 1, 2]}')
        self.assertEqual(value(["jsonb_insert", '{"a": [0, 1, 2]}', ["a", "1"], '"x"', True]),
                         '{"a": [0, 1, "x", 2]}')

    def test_negative_index(self):
        self.assertEqual(value(["jsonb_insert", "[0, 1, 2]", ["-1"], "9"]),
                         "[0, 1, 9, 2]")

    def test_out_of_range(self):
        self.assertEqual(value(["jsonb_insert", "[0, 1, 2]", ["10"], "9"]),
                         "[0, 1, 2, 9]")
        self.assertEqual(value(["jsonb_insert", "[0, 1, 2]", ["-10"], "9"]),
                         "[9, 0, 1, 2]")

    def test_object_field(self):
        self.assertEqual(value(["jsonb_insert", '{"a": 1}', ["a"], "2"]), '{"a": 1}')
        self.assertEqual(value(["jsonb_insert", '{"a": 1}', ["b"], "2"]),
                         '{"a": 1, "b": 2}')

    def test_missing_intermediate_unchanged(self):
        self.assertEqual(value(["jsonb_insert", "{}", ["a", "b"], "1"]), "{}")

    def test_sql_null(self):
        self.assertIsNone(value(["jsonb_insert", None, ["a"], "1"]))
        self.assertIsNone(value(["jsonb_insert", "{}", ["a"], None]))
        self.assertIsNone(value(["jsonb_insert", "{}", ["a"], "1", None]))


class StripNullsTests(unittest.TestCase):
    def test_array_nulls_are_kept(self):
        self.assertEqual(value(["jsonb_strip_nulls", "[1, null, 2]"]), "[1, null, 2]")

    def test_nested(self):
        self.assertEqual(value(["jsonb_strip_nulls", '{"a": null, "b": [null, {"c": null}]}']),
                         '{"b": [null, {}]}')

    def test_scalars(self):
        self.assertEqual(value(["jsonb_strip_nulls", "null"]), "null")
        self.assertIsNone(value(["jsonb_strip_nulls", None]))


class ArrayLengthTests(unittest.TestCase):
    def test_array(self):
        self.assertEqual(value(["jsonb_array_length", "[1, 2]"]), 2)
        self.assertEqual(value(["jsonb_array_length", "[]"]), 0)

    def test_object_errors(self):
        self.assertEqual(error(["jsonb_array_length", "{}"]),
                         "cannot get array length of a non-array")

    def test_scalar_errors(self):
        self.assertEqual(error(["jsonb_array_length", "1"]),
                         "cannot get array length of a scalar")
        self.assertEqual(error(["jsonb_array_length", "null"]),
                         "cannot get array length of a scalar")

    def test_sql_null(self):
        self.assertIsNone(value(["jsonb_array_length", None]))


class TypeofTests(unittest.TestCase):
    def test_types(self):
        self.assertEqual(value(["jsonb_typeof", "{}"]), "object")
        self.assertEqual(value(["jsonb_typeof", "[]"]), "array")
        self.assertEqual(value(["jsonb_typeof", "true"]), "boolean")
        self.assertEqual(value(["jsonb_typeof", "1.5"]), "number")
        self.assertEqual(value(["jsonb_typeof", '"s"']), "string")
        self.assertEqual(value(["jsonb_typeof", "null"]), "null")

    def test_sql_null(self):
        self.assertIsNone(value(["jsonb_typeof", None]))


class RenderTests(unittest.TestCase):
    def test_key_order_length_then_bytes(self):
        self.assertEqual(value(["jsonb", '{"z": 1, "aa": 2}']), '{"z": 1, "aa": 2}')
        self.assertEqual(value(["jsonb", '{"é": 1, "z": 2}']), '{"z": 2, "é": 1}')

    def test_numbers_fixed_point(self):
        self.assertEqual(value(["jsonb", "1e2"]), "100")
        self.assertEqual(value(["jsonb", "1.2300"]), "1.2300")
        self.assertEqual(value(["jsonb", "1e-7"]), "0.0000001")
        self.assertEqual(value(["jsonb", "-0"]), "-0")
        self.assertEqual(value(["jsonb", "0.000"]), "0.000")
        self.assertEqual(value(["jsonb", "1.5e3"]), "1500")
        self.assertEqual(value(["jsonb", "1.5e-3"]), "0.0015")

    def test_duplicate_keys_last_wins(self):
        self.assertEqual(value(["jsonb", '{"a": 1, "a": 2}']), '{"a": 2}')

    def test_string_escapes(self):
        self.assertEqual(value(["jsonb", '"a\\nb\\u0001"']), '"a\\nb\\u0001"')

    def test_rejects_non_json(self):
        self.assertEqual(error(["jsonb", "NaN"]), "invalid input syntax for type json")
        self.assertEqual(error(["jsonb", "Infinity"]), "invalid input syntax for type json")
        self.assertEqual(error(["jsonb", "1 2"]), "invalid input syntax for type json")

    def test_numeric_overflow(self):
        self.assertEqual(error(["jsonb", "1e131072"]), "value overflows numeric format")
        self.assertEqual(value(["jsonb", "1e131071"])[0], "1")

    def test_lone_surrogate(self):
        self.assertEqual(error(["jsonb", '"\\ud800"']), "invalid Unicode escape")
        self.assertEqual(value(["jsonb", '"\\ud83d\\ude00"']), '"\U0001f600"')


if __name__ == "__main__":
    unittest.main()
