import unittest

from strfn.basic import FunctionError
from strfn.registry import FUNCTIONS as F


class BasicTests(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(F["upper"]("abc"), "ABC")
        self.assertEqual(F["lpad"]("7", 3, "0"), "007")
        self.assertEqual(F["split_part"]("a,b,c", ",", 2), "b")
        self.assertIsNone(F["||"]("a", None))
        self.assertEqual(F["format"]("%s-%s", "a", "b"), "a-b")


class ConcatTests(unittest.TestCase):
    def test_nulls_are_ignored_not_printed(self):
        self.assertEqual(F["concat"]("Order ", 1042, " - ", None), "Order 1042 - ")
        self.assertEqual(F["concat"]("a", None, "b", None, 1, True), "ab1true")
        self.assertEqual(F["concat"](), "")

    def test_concat_ws_skips_null_arguments(self):
        self.assertEqual(F["concat_ws"]("-", "a", None, "b"), "a-b")
        self.assertIsNone(F["concat_ws"](None, "a"))
        self.assertEqual(F["concat_ws"](",", None, None), "")

    def test_pipe_is_strict(self):
        self.assertIsNone(F["||"]("a", None))
        self.assertIsNone(F["||"](None, "b"))
        self.assertEqual(F["||"]("a", "b"), "ab")


class PaddingTests(unittest.TestCase):
    def test_lpad_truncates_overlong_input(self):
        self.assertEqual(F["lpad"]("INV-2026-000123", 10), "INV-2026-0")
        self.assertEqual(F["rpad"]("abcdef", 3), "abc")

    def test_multicharacter_fill_is_cycled_and_cut(self):
        self.assertEqual(F["lpad"]("hi", 7, "ab"), "ababahi")
        self.assertEqual(F["lpad"]("hi", 6, "abc"), "abcahi")
        self.assertEqual(F["rpad"]("hi", 7, "ab"), "hiababa")

    def test_negative_and_zero_length(self):
        self.assertEqual(F["lpad"]("abc", -2), "")
        self.assertEqual(F["lpad"]("abc", 0), "")
        self.assertEqual(F["rpad"]("abc", -1), "")

    def test_empty_fill_errors_only_when_padding_needed(self):
        with self.assertRaises(FunctionError):
            F["lpad"]("abc", 10, "")
        with self.assertRaises(FunctionError):
            F["rpad"]("abc", 5, "")
        self.assertEqual(F["lpad"]("abc", 3, ""), "abc")

    def test_strict_null(self):
        self.assertIsNone(F["lpad"](None, 5))
        self.assertIsNone(F["rpad"](None, 5, "x"))


class SplitPartTests(unittest.TestCase):
    def test_basic_and_out_of_range(self):
        self.assertEqual(F["split_part"]("2026-11-20", "-", 2), "11")
        self.assertEqual(F["split_part"]("2026-11-20", "-", 4), "")
        self.assertEqual(F["split_part"]("a,b,", ",", 3), "")
        self.assertEqual(F["split_part"]("a,,", ",", 2), "")

    def test_non_positive_and_empty_delimiter_error(self):
        with self.assertRaises(FunctionError):
            F["split_part"]("a,b", ",", 0)
        with self.assertRaises(FunctionError):
            F["split_part"]("a,b", ",", -1)
        with self.assertRaises(FunctionError):
            F["split_part"]("abc", "", 1)


class SubstringTests(unittest.TestCase):
    def test_substr_edges(self):
        self.assertEqual(F["substr"]("hello", 2), "ello")
        self.assertEqual(F["substr"]("hello", 2, 3), "ell")
        with self.assertRaises(FunctionError):
            F["substr"]("hello", -3, 2)
        self.assertEqual(F["substr"]("hello", -3, 6), "he")
        self.assertEqual(F["substr"]("hello", 0, 2), "h")
        self.assertEqual(F["substr"]("hello", 10), "")
        self.assertEqual(F["substr"]("hello", 1, 0), "")
        self.assertEqual(F["substr"]("hello", 3, 10), "llo")
        with self.assertRaises(FunctionError):
            F["substr"]("hello", 1, -1)

    def test_left_right_negative(self):
        self.assertEqual(F["left"]("abcd", 2), "ab")
        self.assertEqual(F["left"]("abcd", -2), "ab")
        self.assertEqual(F["left"]("ab", 5), "ab")
        self.assertEqual(F["right"]("abcd", 2), "cd")
        self.assertEqual(F["right"]("abcd", -2), "ab")
        self.assertEqual(F["right"]("ab", 5), "ab")

    def test_overlay(self):
        self.assertEqual(F["overlay"]("Txxxxas", "hom", 2, 4), "Thomas")
        self.assertEqual(F["overlay"]("Txxxxas", "hom", 2), "Thomxas")
        self.assertEqual(F["overlay"]("abcdef", "XY", -1, 2), "XYcdef")
        self.assertEqual(F["overlay"]("abcdef", "XY", 10), "abcdefXY")
        with self.assertRaises(FunctionError):
            F["overlay"]("abc", "x", 1, -1)


class TextTests(unittest.TestCase):
    def test_initcap_word_boundaries(self):
        self.assertEqual(F["initcap"]("acme corp"), "Acme Corp")
        self.assertEqual(F["initcap"]("hello12world"), "Hello12World")
        self.assertEqual(F["initcap"]("ABC-def"), "Abc-Def")

    def test_repeat_negative_is_zero(self):
        self.assertEqual(F["repeat"]("ab", 3), "ababab")
        self.assertEqual(F["repeat"]("ab", 0), "")
        self.assertEqual(F["repeat"]("ab", -3), "")

    def test_trims_with_empty_chars_are_noop(self):
        self.assertEqual(F["btrim"]("abc", ""), "abc")
        self.assertEqual(F["ltrim"]("abc", ""), "abc")
        self.assertEqual(F["rtrim"]("abc", ""), "abc")
        self.assertEqual(F["btrim"]("xxhixx", "x"), "hi")

    def test_replace_empty_search_is_noop(self):
        self.assertEqual(F["replace"]("abc", "", "X"), "abc")
        self.assertEqual(F["replace"]("a-b-a", "a", "X"), "X-b-X")

    def test_translate_duplicate_from_keeps_first(self):
        self.assertEqual(F["translate"]("abc", "abc", "xy"), "xy")
        self.assertEqual(F["translate"]("aab", "aab", "112"), "112")
        self.assertEqual(F["translate"]("aab", "aax", "123"), "11b")
        self.assertEqual(F["translate"]("abc", "", "x"), "abc")

    def test_strpos_missing_is_zero(self):
        self.assertEqual(F["strpos"]("abcabc", "bc"), 2)
        self.assertEqual(F["strpos"]("abc", "z"), 0)

    def test_chr_ascii_edges(self):
        self.assertEqual(F["chr"](65), "A")
        self.assertEqual(F["chr"](0), "\x00")
        self.assertEqual(F["ascii"]("A"), 65)
        self.assertEqual(F["ascii"]("é"), 233)
        with self.assertRaises(FunctionError):
            F["ascii"]("")
        with self.assertRaises(FunctionError):
            F["chr"](-1)
        with self.assertRaises(FunctionError):
            F["chr"](0x110000)

    def test_to_hex_negative_is_64bit_complement(self):
        self.assertEqual(F["to_hex"](255), "ff")
        self.assertEqual(F["to_hex"](0), "0")
        self.assertEqual(F["to_hex"](-1), "ffffffffffffffff")
        self.assertEqual(F["to_hex"](-16), "fffffffffffffff0")
        self.assertIsNone(F["to_hex"](None))


class QuotingTests(unittest.TestCase):
    def test_quote_ident(self):
        self.assertEqual(F["quote_ident"]("foo"), "foo")
        self.assertEqual(F["quote_ident"]("Foo"), '"Foo"')
        self.assertEqual(F["quote_ident"]("select"), '"select"')
        self.assertEqual(F["quote_ident"]('a"b'), '"a""b"')
        self.assertIsNone(F["quote_ident"](None))

    def test_quote_literal_strict_and_nullable(self):
        self.assertEqual(F["quote_literal"]("o'reilly"), "'o''reilly'")
        self.assertIsNone(F["quote_literal"](None))
        self.assertEqual(F["quote_nullable"](None), "NULL")
        self.assertEqual(F["quote_nullable"]("a'b"), "'a''b'")

    def test_format_null_renders_empty(self):
        self.assertEqual(F["format"]("%s shipped to %s", "Order 1042", None),
                         "Order 1042 shipped to ")

    def test_format_specs(self):
        self.assertEqual(F["format"]("%2$s %1$s", "a", "b"), "b a")
        self.assertEqual(F["format"]("%1$s%1$s", "z"), "zz")
        self.assertEqual(F["format"]("%5s", "ab"), "   ab")
        self.assertEqual(F["format"]("%-5s|", "ab"), "ab   |")
        self.assertEqual(F["format"]("100%%"), "100%")
        self.assertEqual(F["format"]("%I", "Foo"), '"Foo"')
        self.assertEqual(F["format"]("%L", None), "NULL")
        self.assertEqual(F["format"]("%L", "a'b"), "'a''b'")
        self.assertEqual(F["format"]("%s", True), "true")
        self.assertIsNone(F["format"](None, "a"))

    def test_format_errors(self):
        with self.assertRaises(FunctionError):
            F["format"]("%s%s", "a")
        with self.assertRaises(FunctionError):
            F["format"]("%0$s", "a")
        with self.assertRaises(FunctionError):
            F["format"]("%s %1$s", "a", "b")
        with self.assertRaises(FunctionError):
            F["format"]("%d", 1)
        with self.assertRaises(FunctionError):
            F["format"]("abc%")


class StrictnessTests(unittest.TestCase):
    def test_every_strict_function_returns_null_on_null(self):
        strict_calls = [
            ("length", ("x",)), ("octet_length", ("x",)), ("upper", ("x",)),
            ("lower", ("x",)), ("initcap", ("x",)), ("reverse", ("x",)),
            ("repeat", ("x", 1)), ("left", ("x", 1)), ("right", ("x", 1)),
            ("substr", ("x", 1)), ("strpos", ("x", "y")),
            ("split_part", ("x", "y", 1)), ("lpad", ("x", 5)),
            ("rpad", ("x", 5)), ("btrim", ("x",)), ("ltrim", ("x",)),
            ("rtrim", ("x",)), ("replace", ("x", "a", "b")),
            ("translate", ("x", "a", "b")), ("overlay", ("x", "y", 1)),
            ("chr", (1,)), ("ascii", ("x",)), ("starts_with", ("x", "y")),
            ("to_hex", (1,)),
        ]
        for name, args in strict_calls:
            for i in range(len(args)):
                broken = list(args)
                broken[i] = None
                self.assertIsNone(F[name](*broken),
                                  "%s(%r) should be NULL" % (name, broken))

    def test_registry_complete(self):
        for name in ("concat", "concat_ws", "||", "length", "octet_length",
                     "upper", "lower", "initcap", "reverse", "repeat", "left",
                     "right", "substr", "strpos", "split_part", "lpad", "rpad",
                     "btrim", "ltrim", "rtrim", "replace", "translate",
                     "overlay", "chr", "ascii", "starts_with", "to_hex",
                     "quote_ident", "quote_literal", "quote_nullable",
                     "format", "regexp_replace"):
            self.assertIn(name, F)
            self.assertTrue(callable(F[name]))


class RegexpReplaceTests(unittest.TestCase):
    def test_first_match_is_default(self):
        self.assertEqual(F["regexp_replace"]("a1b2c3", r"\d", "X"), "aXb2c3")

    def test_global_flag(self):
        self.assertEqual(F["regexp_replace"]("a1b2c3", r"\d", "X", "g"), "aXbXcX")

    def test_backreferences_and_escapes(self):
        self.assertEqual(F["regexp_replace"]("abc", "b", r"[\&]"), "a[b]c")
        self.assertEqual(F["regexp_replace"]("abc", "(b)", r"-\1-"), "a-b-c")
        self.assertEqual(F["regexp_replace"]("a1a2", r"a(\d)", r"[\1]", "g"), "[1][2]")
        self.assertEqual(F["regexp_replace"]("abc", "b", r"\\"), "a\\c")
        self.assertEqual(F["regexp_replace"]("ab", r"(x)?b", r"[\1]"), "a[]")

    def test_flags_and_errors(self):
        self.assertEqual(F["regexp_replace"]("Aa", "a", "x", "i"), "xa")
        self.assertEqual(F["regexp_replace"]("a\nb", "^b", "X", "m"), "a\nX")
        with self.assertRaises(FunctionError):
            F["regexp_replace"]("ab", "b", r"\2")
        with self.assertRaises(FunctionError):
            F["regexp_replace"]("a", "a", "b", "x")
        with self.assertRaises(FunctionError):
            F["regexp_replace"]("a", "(unclosed", "x")
        self.assertIsNone(F["regexp_replace"](None, "a", "b"))


if __name__ == "__main__":
    unittest.main()
