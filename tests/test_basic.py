import unittest

from castcall import call


class BasicTests(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(call(["42", "text", "int4"])["value"], "42")
        self.assertEqual(call(["true", "text", "bool"])["value"], "t")
        self.assertEqual(call(["123.456", "numeric", "numeric(5,2)"])["value"], "123.46")

    def test_text_int_input(self):
        self.assertEqual(call([" 42 ", "text", "int4"])["value"], "42")
        self.assertEqual(call(["+42", "text", "int2"])["value"], "42")
        self.assertIn("error", call(["3.7", "text", "int4"]))
        self.assertIn("error", call(["3.0", "text", "int4"]))
        self.assertIn("error", call(["12abc", "text", "int4"]))
        self.assertIn("error", call(["32768", "text", "int2"]))
        self.assertIn("error", call(["-32769", "text", "int2"]))
        self.assertEqual(call(["32767", "text", "int2"])["value"], "32767")

    def test_bool_input(self):
        self.assertIn("error", call(["maybe", "text", "bool"]))
        self.assertIn("error", call(["o", "text", "bool"]))  # ambiguous prefix
        self.assertEqual(call(["of", "text", "bool"])["value"], "f")
        self.assertEqual(call(["YeS", "text", "bool"])["value"], "t")
        self.assertEqual(call(["0", "text", "bool"])["value"], "f")
        self.assertEqual(call([1, "int4", "bool"])["value"], "t")
        self.assertEqual(call([0, "int4", "bool"])["value"], "f")
        # casts PostgreSQL does not have must fail
        self.assertIn("error", call(["1", "numeric", "bool"]))
        self.assertIn("error", call(["1", "float8", "bool"]))
        self.assertIn("error", call([True, "bool", "float8"]))
        self.assertIn("error", call([True, "bool", "numeric"]))
        self.assertEqual(call([True, "bool", "int4"])["value"], "1")
        self.assertEqual(call([True, "bool", "text"])["value"], "t")

    def test_numeric_typmod(self):
        self.assertEqual(call(["123.456", "numeric", "numeric(5,2)"])["value"], "123.46")
        self.assertIn("error", call(["1234.5", "numeric", "numeric(5,2)"]))
        self.assertIn("error", call(["999.999", "numeric", "numeric(5,2)"]))
        self.assertEqual(call(["999.99", "numeric", "numeric(5,2)"])["value"], "999.99")
        self.assertEqual(call(["0", "numeric", "numeric(5,2)"])["value"], "0.00")

    def test_rounding(self):
        # numeric -> int rounds half away from zero
        self.assertEqual(call(["2.5", "numeric", "int4"])["value"], "3")
        self.assertEqual(call(["-2.5", "numeric", "int4"])["value"], "-3")
        # float8 -> int rounds half to even
        self.assertEqual(call(["2.5", "float8", "int4"])["value"], "2")
        self.assertEqual(call(["3.5", "float8", "int4"])["value"], "4")
        self.assertIn("error", call(["1e30", "float8", "int8"]))

    def test_float8_out(self):
        self.assertEqual(call(["1.0", "float8", "text"])["value"], "1")
        self.assertEqual(call(["0.1", "float8", "text"])["value"], "0.1")
        self.assertEqual(call(["0.30000000000000004", "float8", "text"])["value"],
                         "0.30000000000000004")
        self.assertEqual(call(["100000", "float8", "text"])["value"], "100000")
        self.assertEqual(call(["1e15", "float8", "text"])["value"], "1e+15")
        self.assertEqual(call(["1e14", "float8", "text"])["value"], "100000000000000")
        self.assertEqual(call(["0.0001", "float8", "text"])["value"], "0.0001")
        self.assertEqual(call(["0.00001", "float8", "text"])["value"], "1e-05")
        self.assertEqual(call(["-2.5", "float8", "text"])["value"], "-2.5")
        self.assertEqual(call(["1e400", "text", "float8"])["error"].__class__, str)
        self.assertIn("error", call(["1e400", "text", "float8"]))

    def test_float8_numeric(self):
        # float8 -> numeric keeps 15 significant digits
        self.assertEqual(call(["0.30000000000000004", "float8", "numeric"])["value"], "0.3")
        self.assertEqual(call(["0.1", "float8", "numeric"])["value"], "0.1")
        self.assertEqual(call(["1e6", "numeric", "text"])["value"], "1000000")

    def test_char_varchar(self):
        r = call(["ab", "text", "char(5)"])
        self.assertEqual(r["value"], "ab   ")
        self.assertEqual(r["length"], 2)  # length() ignores trailing spaces
        self.assertEqual(call(["abcdef", "text", "char(3)"])["value"], "abc")
        self.assertEqual(call(["abcdef", "text", "varchar(3)"])["value"], "abc")
        self.assertEqual(call(["abc", "text", "varchar(3)"])["value"], "abc")


if __name__ == "__main__":
    unittest.main()
