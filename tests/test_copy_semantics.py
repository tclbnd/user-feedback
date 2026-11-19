"""Regression tests pinning PostgreSQL COPY ... WITH (FORMAT csv) semantics.

The cases cover the partner-file pain points (commas and quotes in names,
NULL vs empty string, embedded newlines, CRLF, custom NULL/ESCAPE,
HEADER, FORCE_NULL / FORCE_NOT_NULL) plus the error wording the server
emits for malformed input and invalid options.
"""
import unittest

from csvcopy.loader import CopyError, copy_from
from csvcopy.options import resolve
from csvcopy.parser import parse

TEXT3 = {"columns": [{"name": "a", "type": "text"},
                     {"name": "b", "type": "text"},
                     {"name": "c", "type": "text"}]}
INT_TEXT = {"columns": [{"name": "id", "type": "int"},
                        {"name": "v", "type": "text"}]}
BOOL_T = {"columns": [{"name": "id", "type": "int"},
                      {"name": "flag", "type": "bool"}]}
NUM_T = {"columns": [{"name": "id", "type": "int"},
                     {"name": "amt", "type": "numeric"}]}


def fields(data, options=None):
    return [f for f, _line in parse(data, resolve(options))]


class ParsingTests(unittest.TestCase):
    def test_comma_inside_quotes_stays_in_one_field(self):
        self.assertEqual(
            fields('2,"Smith, J.","",0\n'),
            [[("2", False), ("Smith, J.", True), ("", True), ("0", False)]],
        )

    def test_doubled_quote_is_one_quote(self):
        self.assertEqual(
            fields('3,"The ""Best"" Co",regular,\n'),
            [[("3", False), ('The "Best" Co', True),
              ("regular", False), ("", False)]],
        )

    def test_embedded_lf_inside_quotes_is_data(self):
        self.assertEqual(
            fields('1,"two\nlines",x\n'),
            [[("1", False), ("two\nlines", True), ("x", False)]],
        )

    def test_embedded_crlf_inside_quotes_is_kept_verbatim(self):
        self.assertEqual(
            fields('"a\r\nb"\n'),
            [[("a\r\nb", True)]],
        )

    def test_crlf_and_lf_record_terminators(self):
        self.assertEqual(
            fields("a,b\r\nc,d\r\ne,f\n"),
            [[("a", False), ("b", False)],
             [("c", False), ("d", False)],
             [("e", False), ("f", False)]],
        )

    def test_lone_cr_in_unquoted_field_is_an_error(self):
        with self.assertRaisesRegex(CopyError, "unquoted carriage return found in data"):
            copy_from(TEXT3, "a,b\rc\n")

    def test_unterminated_quoted_field_is_an_error(self):
        with self.assertRaisesRegex(CopyError, "unterminated CSV quoted field"):
            copy_from(TEXT3, 'a,"oops,c\n')

    def test_eof_quote_corner_cases(self):
        # A lone quote at EOF is an unterminated quoted field.
        with self.assertRaisesRegex(CopyError, "unterminated CSV quoted field"):
            fields('"')
        # A doubled quote at EOF reads as an empty quoted field: the first
        # quote opens it, the second closes it (no byte follows to escape).
        self.assertEqual(fields('""'), [[("", True)]])
        # An escape left dangling at EOF still leaves the field open.
        with self.assertRaisesRegex(CopyError, "unterminated CSV quoted field"):
            fields('"a\\', {"escape": "\\"})

    def test_quote_not_at_field_start_is_literal(self):
        self.assertEqual(
            fields('a,ab"cd,c\n'),
            [[("a", False), ('ab"cd', False), ("c", False)]],
        )

    def test_distinct_escape_character(self):
        # Backslash escapes the quote inside the quoted field.
        self.assertEqual(
            fields('"a\\"b",z\n', {"escape": "\\"}),
            [[('a"b', True), ("z", False)]],
        )
        # Backslash outside a quoted field is ordinary data.
        self.assertEqual(fields("a\\b\n", {"escape": "\\"}),
                         [[("a\\b", False)]])

    def test_blank_lines_and_eof(self):
        # Empty input has no records; a trailing terminator adds none.
        self.assertEqual(fields(""), [])
        self.assertEqual(fields("a\n"), [[("a", False)]])
        # A blank line is a one-field record containing the empty string.
        self.assertEqual(fields("\n"), [[("", False)]])
        self.assertEqual(fields("a\n\nb\n"),
                         [[("a", False)], [("", False)], [("b", False)]])

    def test_custom_delimiter(self):
        self.assertEqual(fields("a|b|c\n", {"delimiter": "|"}),
                         [[("a", False), ("b", False), ("c", False)]])


class NullHandlingTests(unittest.TestCase):
    def test_default_null_distinguishes_unquoted_and_quoted_empty(self):
        rows = copy_from(TEXT3, 'a,,c\na,"",c\n')
        self.assertIsNone(rows[0]["b"])
        self.assertEqual(rows[1]["b"], "")

    def test_custom_null_string(self):
        rows = copy_from(TEXT3, '\\N,"\\N",x\n', {"null": "\\N"})
        self.assertIsNone(rows[0]["a"])          # unquoted NULL marker
        self.assertEqual(rows[0]["b"], "\\N")    # quoted marker is literal

    def test_force_null_applies_to_quoted_fields(self):
        rows = copy_from(TEXT3, 'a,"",c\n', {"force_null": ["b"]})
        self.assertIsNone(rows[0]["b"])

    def test_force_not_null_keeps_unquoted_null_marker(self):
        rows = copy_from(TEXT3, "a,,c\n", {"force_not_null": ["b"]})
        self.assertEqual(rows[0]["b"], "")
        rows = copy_from(TEXT3, "a,\\N,c\n",
                         {"null": "\\N", "force_not_null": ["b"]})
        self.assertEqual(rows[0]["b"], "\\N")

    def test_force_null_and_force_not_null_cannot_share_a_column(self):
        with self.assertRaisesRegex(CopyError, "FORCE_NULL and FORCE_NOT_NULL"):
            resolve({"force_null": ["b"], "force_not_null": ["b"]},
                    ["a", "b", "c"])


class HeaderAndFieldCountTests(unittest.TestCase):
    def test_header_skips_first_record_even_when_multiline(self):
        rows = copy_from(TEXT3, '"h1\nx",h2,h3\n1,2,3\n', {"header": True})
        self.assertEqual(rows, [{"a": "1", "b": "2", "c": "3"}])

    def test_missing_column_reports_physical_end_line(self):
        with self.assertRaisesRegex(
            CopyError,
            'missing data for column "c"\\nCONTEXT:  COPY .*, line 4',
        ):
            copy_from(TEXT3, '"a\nb",x,y\n1,2\n')

    def test_extra_column_is_an_error(self):
        with self.assertRaisesRegex(CopyError, "extra data after last expected column"):
            copy_from(TEXT3, "1,2,3,4\n")


class TypeInputTests(unittest.TestCase):
    def test_bad_integer_uses_postgres_wording(self):
        with self.assertRaisesRegex(
            CopyError,
            '(?s)invalid input syntax for type integer: "x".*line 2, column id',
        ):
            copy_from(INT_TEXT, "x,a\n")

    def test_integer_accepts_sign_and_leading_space(self):
        rows = copy_from(
            {"columns": [{"name": "id", "type": "int"}]},
            "1\n-2\n+3\n 4\n",
        )
        self.assertEqual([r["id"] for r in rows], [1, -2, 3, 4])

    def test_integer_rejects_underscores_and_trailing_space(self):
        for bad in ("1_0", "1 ", "1.0"):
            with self.assertRaises(CopyError):
                copy_from({"columns": [{"name": "id", "type": "int"}]}, bad + "\n")

    def test_numeric_forms_are_kept_as_input_text(self):
        rows = copy_from(NUM_T, "1,100.50\n2,-3\n3,1e4\n4,.5\n")
        self.assertEqual([r["amt"] for r in rows],
                         ["100.50", "-3", "1e4", ".5"])

    def test_bad_numeric_is_an_error(self):
        with self.assertRaisesRegex(CopyError, 'type numeric: "1.2.3"'):
            copy_from(NUM_T, "1,1.2.3\n")

    def test_boolean_inputs(self):
        rows = copy_from(BOOL_T, "1,true\n2,f\n3,YES\n4,0\n5, off \n")
        self.assertEqual([r["flag"] for r in rows],
                         [True, False, True, False, False])

    def test_bad_boolean_is_an_error(self):
        with self.assertRaisesRegex(CopyError, 'type boolean: "maybe"'):
            copy_from(BOOL_T, "1,maybe\n")


class OptionValidationTests(unittest.TestCase):
    def test_single_one_byte_character_required(self):
        for option in ("delimiter", "quote", "escape"):
            with self.assertRaisesRegex(CopyError, "single one-byte character"):
                resolve({option: "ab"}, ["a"])
        with self.assertRaisesRegex(CopyError, "single one-byte character"):
            resolve({"delimiter": "\u00e9"}, ["a"])

    def test_quote_and_escape_must_differ_from_delimiter(self):
        with self.assertRaisesRegex(CopyError, "quote parameter cannot be the same"):
            resolve({"delimiter": "|", "quote": "|"}, ["a"])
        with self.assertRaisesRegex(CopyError, "escape parameter cannot be the same"):
            resolve({"delimiter": "|", "escape": "|"}, ["a"])

    def test_null_constraints(self):
        with self.assertRaisesRegex(CopyError, "must not contain carriage return or newline"):
            resolve({"null": "a\nb"}, ["a"])
        with self.assertRaisesRegex(CopyError, "quote character must not appear"):
            resolve({"null": 'a"b'}, ["a"])

    def test_force_columns_must_exist_and_be_unique(self):
        with self.assertRaisesRegex(CopyError, 'column "nope".* does not exist'):
            resolve({"force_null": ["nope"]}, ["a"])
        with self.assertRaisesRegex(CopyError, 'appears more than once'):
            resolve({"force_null": ["a", "a"]}, ["a"])

    def test_header_must_be_boolean(self):
        with self.assertRaisesRegex(CopyError, "header option must be a boolean"):
            resolve({"header": "yes"}, ["a"])

    def test_escape_defaults_to_quote(self):
        opts = resolve()
        self.assertEqual(opts["escape"], opts["quote"])


class PartnerFileTest(unittest.TestCase):
    def test_example_partner_file(self):
        import json
        import os

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "examples", "partners.json"),
                  encoding="utf-8") as f:
            job = json.load(f)
        with open(os.path.join(here, "examples", "partners.csv"),
                  encoding="utf-8", newline="") as f:
            data = f.read()
        self.assertEqual(copy_from(job["table"], data, job.get("options")), [
            {"id": 1, "name": "Acme", "note": None, "credit": "100.50"},
            {"id": 2, "name": "Smith, J.", "note": "", "credit": "0"},
            {"id": 3, "name": 'The "Best" Co', "note": "regular",
             "credit": None},
        ])


if __name__ == "__main__":
    unittest.main()
