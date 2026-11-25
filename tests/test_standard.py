import struct
import unittest

from fitskit.fits import FitsError, parse, value


def card(key, val):
    return (key.ljust(8) + "= " + str(val)).ljust(80)


def hdu(*cards):
    text = "".join(c.ljust(80) for c in cards) + "END".ljust(80)
    return (text + " " * (-len(text) % 2880)).encode("ascii")


def data_unit(payload):
    return payload + b"\x00" * (-len(payload) % 2880)


def primary_with_data(payload, bitpix, naxis1, naxis2, *extra):
    cards = ["SIMPLE  =                    T", card("BITPIX", bitpix), card("NAXIS", 2),
             card("NAXIS1", naxis1), card("NAXIS2", naxis2)]
    return hdu(*cards, *extra) + data_unit(payload)


def image_ext(payload, bitpix, naxis1, naxis2, *extra):
    cards = [card("XTENSION", "'IMAGE'"), card("BITPIX", bitpix), card("NAXIS", 2),
             card("NAXIS1", naxis1), card("NAXIS2", naxis2), card("PCOUNT", 0), card("GCOUNT", 1)]
    return hdu(*cards, *extra) + data_unit(payload)


class QuotedStringTests(unittest.TestCase):
    def test_apostrophe_inside_string(self):
        self.assertEqual(value("'Barnard''s Loop'"), "Barnard's Loop")

    def test_apostrophe_with_comment(self):
        self.assertEqual(value("'Barnard''s Loop' / nebula"), "Barnard's Loop")

    def test_doubled_quote_is_one_quote(self):
        self.assertEqual(value("''''"), "'")

    def test_object_card_keeps_full_name(self):
        out = parse(hdu("SIMPLE  =                    T", card("BITPIX", 8), card("NAXIS", 0),
                        card("OBJECT", "'Barnard''s Loop'")))
        self.assertEqual(out["hdu"][0]["object"], "Barnard's Loop")

    def test_empty_string_stays_a_string(self):
        self.assertEqual(value("''"), "")

    def test_numeric_and_logical(self):
        self.assertEqual(value("  120 / exptime"), 120)
        self.assertEqual(value("  -32"), -32)
        self.assertEqual(value("                   T"), True)
        self.assertEqual(value("1.5D2"), 150.0)


class ScalingTests(unittest.TestCase):
    def test_unsigned_16bit(self):
        # Unsigned 16-bit is stored as signed values 0..65535 minus 32768.
        payload = struct.pack(">4h", -32768, -1, 0, 32767)
        out = parse(primary_with_data(payload, 16, 4, 1, card("BZERO", 32768), card("BSCALE", 1)))
        hdu0 = out["hdu"][0]
        self.assertEqual((hdu0["min"], hdu0["max"]), ("0", "65535"))

    def test_signed_16bit_without_bzero(self):
        payload = struct.pack(">3h", -5, 0, 7)
        out = parse(primary_with_data(payload, 16, 3, 1))
        self.assertEqual((out["hdu"][0]["min"], out["hdu"][0]["max"]), ("-5", "7"))

    def test_scale_and_offset(self):
        payload = struct.pack(">2h", 0, 4)
        out = parse(primary_with_data(payload, 16, 2, 1, card("BZERO", 100), card("BSCALE", 2)))
        self.assertEqual((out["hdu"][0]["min"], out["hdu"][0]["max"]), ("100", "108"))


class FloatTests(unittest.TestCase):
    def test_float32_pixels(self):
        payload = struct.pack(">4f", -2.0, 0.0, 1.5, 96.5)
        out = parse(primary_with_data(payload, -32, 4, 1))
        self.assertEqual((out["hdu"][0]["min"], out["hdu"][0]["max"]), ("-2", "96.5"))

    def test_float64_pixels(self):
        payload = struct.pack(">2d", 1e-3, 2e30)
        out = parse(primary_with_data(payload, -64, 2, 1))
        self.assertEqual((out["hdu"][0]["min"], out["hdu"][0]["max"]), ("0.001", "2e+30"))

    def test_float_nan_counts_as_blank(self):
        payload = struct.pack(">3f", float("nan"), 2.0, 4.0)
        out = parse(primary_with_data(payload, -32, 3, 1))
        self.assertEqual(out["hdu"][0]["blank"], 1)
        self.assertEqual(out["hdu"][0]["min"], "2")


class BlankTests(unittest.TestCase):
    def test_blank_skipped_in_statistics(self):
        payload = struct.pack(">4h", 5, -999, 10, 7)
        out = parse(primary_with_data(payload, 16, 4, 1, card("BLANK", -999)))
        hdu0 = out["hdu"][0]
        self.assertEqual(hdu0["min"], "5")
        self.assertEqual(hdu0["blank"], 1)

    def test_blank_is_stored_value_not_physical(self):
        payload = struct.pack(">3h", -32768, -32767, 32767)
        out = parse(primary_with_data(payload, 16, 3, 1, card("BZERO", 32768), card("BLANK", -32768)))
        hdu0 = out["hdu"][0]
        self.assertEqual(hdu0["min"], "1")
        self.assertEqual(hdu0["blank"], 1)

    def test_no_blank_card_means_zero(self):
        payload = struct.pack(">2h", -10, 4)
        out = parse(primary_with_data(payload, 16, 2, 1))
        self.assertEqual(out["hdu"][0]["blank"], 0)

    def test_blank_ignored_for_float_data(self):
        payload = struct.pack(">2f", -1.0, 2.0)
        out = parse(primary_with_data(payload, -32, 2, 1, card("BLANK", -1)))
        self.assertEqual(out["hdu"][0]["blank"], 0)


class BlockPackingTests(unittest.TestCase):
    def test_extension_after_data_padding(self):
        payload = struct.pack(">1200h", *range(1200))
        data = (primary_with_data(payload, 16, 40, 30)
                + image_ext(struct.pack(">2f", 1.0, 2.0), -32, 2, 1))
        out = parse(data)
        self.assertEqual(out["hdus"], 2)
        self.assertEqual(out["extensions"], ["IMAGE"])
        self.assertEqual(out["hdu"][1]["header_offset"], 5760)
        self.assertTrue(all(h["complete"] for h in out["hdu"]))
        self.assertEqual((out["hdu"][1]["min"], out["hdu"][1]["max"]), ("1", "2"))

    def test_zero_sized_data_unit_is_not_padded(self):
        data = hdu("SIMPLE  =                    T", card("BITPIX", 8), card("NAXIS", 0))
        data += image_ext(struct.pack(">2B", 3, 9), 8, 2, 1)
        out = parse(data)
        self.assertEqual(out["hdu"][1]["header_offset"], 2880)

    def test_trailing_zero_block_is_end_of_file(self):
        data = primary_with_data(struct.pack(">1h", 7), 16, 1, 1)
        out = parse(data + bytes(2880))
        self.assertEqual(out["hdus"], 1)

    def test_truncated_data_is_incomplete(self):
        payload = struct.pack(">4h", 1, 2, 3, 4)
        data = primary_with_data(payload, 16, 4, 1)
        out = parse(data[:2880 + 4])
        self.assertFalse(out["hdu"][0]["complete"])
        self.assertIsNone(out["hdu"][0]["min"])


class HeaderTests(unittest.TestCase):
    def test_truncated_header(self):
        with self.assertRaises(FitsError):
            parse(hdu("SIMPLE  =                    T") + b" " * 1000)

    def test_bad_bitpix(self):
        with self.assertRaises(FitsError):
            parse(hdu("SIMPLE  =                    T", card("BITPIX", 12), card("NAXIS", 0)))

    def test_comment_and_hierarch_cards_skipped(self):
        out = parse(hdu("SIMPLE  =                    T", card("BITPIX", 8), card("NAXIS", 0),
                        "COMMENT some note", "HIERARCH KEY = value"))
        self.assertEqual(out["hdus"], 1)


if __name__ == "__main__":
    unittest.main()
