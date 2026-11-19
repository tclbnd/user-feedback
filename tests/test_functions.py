import unittest

from bincall import call


def value(*c):
    return call(list(c))["value"]


def error(*c):
    return call(list(c))["error"]


class FunctionTests(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(value("encode", "\\x0001ff", "hex"), "0001ff")
        self.assertEqual(value("decode", "MTIz", "base64"), "\\x313233")
        self.assertEqual(value("length", "\\x123456"), "3")
        self.assertEqual(value("||", "\\x1234", "\\x56"), "\\x123456")
        self.assertEqual(value("bit_count", "B'10110'"), "3")
        self.assertIsNone(value("length", None))

    def test_bytea_input(self):
        self.assertEqual(value("bytea", "\\x1234"), "\\x1234")
        self.assertEqual(value("bytea", "\\xABCD"), "\\xabcd")
        self.assertEqual(value("bytea", "abc"), "\\x616263")
        self.assertEqual(value("bytea", "\\000\\001"), "\\x0001")
        self.assertEqual(value("bytea", "\\1\\12\\123"), "\\x010a53")
        self.assertEqual(value("bytea", "\\\\"), "\\x5c")
        self.assertEqual(value("bytea", "\\377"), "\\xff")
        self.assertEqual(error("bytea", "\\4"), "invalid input syntax for type bytea")
        self.assertEqual(error("bytea", "\\400"), "invalid input syntax for type bytea")
        self.assertEqual(error("bytea", "abc\\"), "invalid input syntax for type bytea")
        self.assertEqual(error("bytea", "\\x123"), "invalid hexadecimal data: odd number of digits")
        self.assertEqual(error("bytea", "\\x12g4"), 'invalid hexadecimal digit: "g"')

    def test_bytea_literal_parse(self):
        self.assertEqual(value("length", "\\x 12 34"), "2")
        self.assertEqual(error("length", "\\x123"), "invalid hexadecimal data: odd number of digits")
        self.assertEqual(error("length", "\\xzz"), 'invalid hexadecimal digit: "z"')
        self.assertEqual(error("length", "B'102'"), '"2" is not a valid binary digit')

    def test_encode_escape(self):
        self.assertEqual(value("encode", "\\x00615c", "escape"), "\\000a\\\\")
        self.assertEqual(value("encode", "\\x1f207e7fff27", "escape"), "\\037 ~\\177\\377'")
        self.assertEqual(value("encode", "\\x", "escape"), "")

    def test_encode_base64_folding(self):
        # 57 bytes -> 76 chars + newline; 60 bytes -> 76 chars + newline + 4
        self.assertEqual(value("encode", "\\x" + "41" * 57, "base64"), "QUFB" * 19 + "\n")
        self.assertEqual(value("encode", "\\x" + "41" * 60, "base64"), "QUFB" * 19 + "\nQUFB")
        self.assertEqual(value("encode", "\\x41", "base64"), "QQ==")
        self.assertEqual(value("encode", "\\x4142", "base64"), "QUI=")
        self.assertEqual(value("encode", "\\x", "base64"), "")

    def test_encode_format_name(self):
        self.assertEqual(value("encode", "\\x12", "HEX"), "12")
        self.assertEqual(value("encode", "\\x12", "Base64"), "Eg==")
        self.assertEqual(error("encode", "\\x12", "foo"), 'unrecognized encoding: "foo"')

    def test_decode_hex(self):
        self.assertEqual(value("decode", " 12 34\n", "hex"), "\\x1234")
        self.assertEqual(value("decode", "AB", "hex"), "\\xab")
        self.assertEqual(error("decode", "abc", "hex"), "invalid hexadecimal data: odd number of digits")
        self.assertEqual(error("decode", "12x4", "hex"), 'invalid hexadecimal digit: "x"')

    def test_decode_base64(self):
        self.assertEqual(value("decode", "TWFu", "base64"), "\\x4d616e")
        self.assertEqual(value("decode", "TQ==", "base64"), "\\x4d")
        self.assertEqual(value("decode", "TWE=", "base64"), "\\x4d61")
        self.assertEqual(value("decode", "TQ", "base64"), "\\x4d")
        self.assertEqual(value("decode", "TW Fu", "base64"), "\\x4d616e")
        self.assertEqual(value("decode", "", "base64"), "\\x")
        self.assertEqual(value("decode", "QQ==\n", "base64"), "\\x41")
        self.assertEqual(value("decode", "QUFB\nQQ==", "base64"), "\\x41414141")
        self.assertEqual(error("decode", "T!", "base64"),
                         'invalid symbol "!" found while decoding base64 sequence')
        self.assertEqual(error("decode", "T", "base64"),
                         'invalid symbol "T" found while decoding base64 sequence')

    def test_decode_escape(self):
        self.assertEqual(value("decode", "\\000a", "escape"), "\\x0061")
        self.assertEqual(value("decode", "\\\\", "escape"), "\\x5c")
        self.assertEqual(error("decode", "\\9", "escape"), "invalid input syntax for type bytea")

    def test_get_set_byte(self):
        self.assertEqual(value("get_byte", "\\x1234567890", 4), "144")
        self.assertEqual(value("get_byte", "\\x1234567890", 0), "18")
        self.assertEqual(error("get_byte", "\\x1234", 2), "index 2 out of valid range, 0..1")
        self.assertEqual(error("get_byte", "\\x1234", -1), "index -1 out of valid range, 0..1")
        self.assertEqual(error("get_byte", "\\x", 0), "index 0 out of valid range, 0..-1")
        self.assertEqual(value("set_byte", "\\x1234", 1, 255), "\\x12ff")
        self.assertEqual(value("set_byte", "\\x1234", 0, 256), "\\x0034")
        self.assertEqual(error("set_byte", "\\x1234", 5, 0), "index 5 out of valid range, 0..1")

    def test_get_set_bit_bytea(self):
        self.assertEqual(value("get_bit", "\\x80", 0), "1")
        self.assertEqual(value("get_bit", "\\x80", 7), "0")
        self.assertEqual(value("get_bit", "\\x1234567890", 30), "0")
        self.assertEqual(error("get_bit", "\\x80", 8), "index 8 out of valid range, 0..7")
        self.assertEqual(error("get_bit", "\\x80", -1), "index -1 out of valid range, 0..7")
        self.assertEqual(value("set_bit", "\\x00", 0, 1), "\\x80")
        self.assertEqual(value("set_bit", "\\xff", 3, 0), "\\xef")
        self.assertEqual(error("set_bit", "\\x00", 0, 2), "new bit must be 0 or 1")
        self.assertEqual(error("set_bit", "\\x00", 9, 1), "index 9 out of valid range, 0..7")

    def test_get_set_bit_bits(self):
        self.assertEqual(value("get_bit", "B'101'", 0), "1")
        self.assertEqual(value("get_bit", "B'101'", 2), "1")
        self.assertEqual(error("get_bit", "B'101'", 3), "bit index 3 out of valid range (0..2)")
        self.assertEqual(error("get_bit", "B'101'", -1), "bit index -1 out of valid range (0..2)")
        self.assertEqual(value("set_bit", "B'000'", 1, 1), "010")
        self.assertEqual(error("set_bit", "B'000'", 3, 1), "bit index 3 out of valid range (0..2)")
        self.assertEqual(error("set_bit", "B'000'", 0, 2), "new bit must be 0 or 1")

    def test_lengths(self):
        self.assertEqual(value("length", "\\x123456"), "3")
        self.assertEqual(value("length", "B'101'"), "3")
        self.assertEqual(value("length", "abc"), "3")
        self.assertEqual(value("bit_length", "\\x1234"), "16")
        self.assertEqual(value("bit_length", "B'101'"), "3")
        self.assertEqual(value("bit_length", "abc"), "24")
        self.assertEqual(value("bit_count", "\\xff01"), "9")
        self.assertEqual(value("bit_count", "B'10110'"), "3")

    def test_substr(self):
        self.assertEqual(value("substr", "\\x12345678", 2, 2), "\\x3456")
        self.assertEqual(value("substr", "\\x12345678", 2), "\\x345678")
        self.assertEqual(value("substr", "\\x12345678", 0, 3), "\\x1234")
        self.assertEqual(value("substr", "\\x12345678", -2, 4), "\\x12")
        self.assertEqual(value("substr", "\\x12345678", 9), "\\x")
        self.assertEqual(error("substr", "\\x12345678", 1, -1), "negative substring length not allowed")
        self.assertEqual(value("substr", "B'10110'", 2, 2), "01")

    def test_overlay(self):
        self.assertEqual(value("overlay", "\\x12345678", "\\x99", 2), "\\x12995678")
        self.assertEqual(value("overlay", "\\x12345678", "\\x99", 2, 2), "\\x129978")
        self.assertEqual(value("overlay", "\\x1234", "\\x99", 10), "\\x123499")
        self.assertEqual(value("overlay", "\\x1234", "\\x99", 0), "\\x9934")
        self.assertEqual(error("overlay", "\\x1234", "\\x99", 1, -1), "negative substring length not allowed")

    def test_btrim(self):
        self.assertEqual(value("btrim", "\\x001200", "\\x00"), "\\x12")
        self.assertEqual(value("btrim", "\\x201220", "\\x20"), "\\x12")
        self.assertEqual(value("btrim", "\\x2020"), "\\x")

    def test_concat(self):
        self.assertEqual(value("||", "\\x1234", "\\x56"), "\\x123456")
        self.assertEqual(value("||", "B'10'", "B'1'"), "101")
        self.assertEqual(value("||", "ab", "cd"), "abcd")
        self.assertEqual(error("||", "\\x12", "B'1'"), "operator does not exist: bytea || bit")

    def test_bit_cast(self):
        self.assertEqual(value("bit", "B'101'", 5), "10100")
        self.assertEqual(value("bit", "B'10110'", 3), "101")
        self.assertEqual(value("bit", "B'101'", 3), "101")

    def test_bitwise_ops(self):
        self.assertEqual(value("&", "B'10001'", "B'01101'"), "00001")
        self.assertEqual(value("|", "B'10001'", "B'01101'"), "11101")
        self.assertEqual(value("#", "B'10001'", "B'01101'"), "11100")
        self.assertEqual(value("~", "B'1010'"), "0101")
        self.assertEqual(error("&", "B'101'", "B'10101'"), "bit strings must be of equal length")
        self.assertEqual(error("|", "B'1'", "B'01'"), "bit strings must be of equal length")

    def test_shifts(self):
        self.assertEqual(value("<<", "B'1010'", 2), "1000")
        self.assertEqual(value(">>", "B'1010'", 2), "0010")
        self.assertEqual(value("<<", "B'1010'", 4), "0000")
        self.assertEqual(value("<<", "B'1010'", 9), "0000")
        self.assertEqual(value(">>", "B'1010'", 1), "0101")

    def test_strict_and_unknown(self):
        self.assertIsNone(value("length", None))
        self.assertIsNone(value("get_byte", "\\x12", None))
        self.assertIsNone(value("||", None, None))
        self.assertEqual(error("nosuchfunc", "\\x12"), "function nosuchfunc(bytea) does not exist")


if __name__ == "__main__":
    unittest.main()
