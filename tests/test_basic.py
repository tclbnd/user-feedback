import unittest
import zlib

from pnmkit.pnm import PnmError, parse, scale


def crc(pixels):
    return "%08x" % zlib.crc32(b"".join(bytes(p) for p in pixels))


class BasicTests(unittest.TestCase):
    def test_ppm_size(self):
        out = parse(b"P6\n2 1\n255\n" + bytes([1, 2, 3, 4, 5, 6]))
        self.assertEqual((out["images"][0]["width"], out["images"][0]["height"]), (2, 1))

    def test_bad_magic(self):
        with self.assertRaises(PnmError):
            parse(b"XX 1 1")

    def test_empty_stream(self):
        with self.assertRaises(PnmError):
            parse(b"")


class HeaderTests(unittest.TestCase):
    def test_comments_classic(self):
        out = parse(b"P6\n# leading comment\n2 2\n# mid comment\n255\n" + bytes(range(12)))
        img = out["images"][0]
        self.assertEqual((img["width"], img["height"]), (2, 2))

    def test_inline_and_trailing_comments(self):
        out = parse(b"P2 # after magic\n3 1 # dims\n15\n1 7 15 # tail\n")
        want = [(scale(v, 15),) * 3 for v in (1, 7, 15)]
        self.assertEqual(out["images"][0]["crc32"], crc(want))

    def test_pam_comments_and_reordered_header(self):
        pam = (b"P7\n# a comment\nMAXVAL 255\nDEPTH 3\nHEIGHT 1 # trailing\nWIDTH 1\n"
               b"TUPLTYPE RGB\nENDHDR\n" + bytes([1, 2, 3]))
        img = parse(pam)["images"][0]
        self.assertEqual((img["width"], img["height"], img["maxval"]), (1, 1, 255))
        self.assertFalse(img["alpha"])
        self.assertEqual(img["crc32"], crc([(1, 2, 3)]))


class ScalingTests(unittest.TestCase):
    def test_halves_round_up(self):
        self.assertEqual(scale(1, 2), 128)       # 127.5 -> 128

    def test_endpoints(self):
        self.assertEqual(scale(0, 255), 0)
        self.assertEqual(scale(255, 255), 255)
        self.assertEqual(scale(1, 1), 255)

    def test_arbitrary_maxval(self):
        self.assertEqual(scale(1, 3), 85)
        self.assertEqual(scale(2, 3), 170)
        self.assertEqual(scale(254, 254), 255)
        self.assertEqual(scale(1, 254), 1)

    def test_maxval_not_255_raster(self):
        out = parse(b"P2\n3 1\n15\n0 7 15\n")
        want = [(scale(v, 15),) * 3 for v in (0, 7, 15)]
        self.assertEqual(out["images"][0]["crc32"], crc(want))


class PbmTests(unittest.TestCase):
    def test_plain_inversion(self):  # 1 is black, 0 is white
        out = parse(b"P1\n2 2\n1 0 0 1\n")
        want = [(0, 0, 0), (255, 255, 255), (255, 255, 255), (0, 0, 0)]
        self.assertEqual(out["images"][0]["crc32"], crc(want))

    def test_raw_inversion(self):
        out = parse(b"P4\n8 1\n" + bytes([0b10101010]))
        want = [((0 if i % 2 == 0 else 255),) * 3 for i in range(8)]
        self.assertEqual(out["images"][0]["crc32"], crc(want))

    def test_raw_row_padding(self):  # width 5 -> one byte per row, padding ignored
        out = parse(b"P4\n5 2\n" + bytes([0b11111000, 0b00000000]))
        want = [(0, 0, 0)] * 5 + [(255, 255, 255)] * 5
        img = out["images"][0]
        self.assertEqual((img["width"], img["height"]), (5, 2))
        self.assertEqual(img["crc32"], crc(want))


class SixteenBitTests(unittest.TestCase):
    def test_big_endian_grayscale(self):
        data = b"P5\n3 1\n1000\n" + (0).to_bytes(2, "big") + (500).to_bytes(2, "big") \
               + (1000).to_bytes(2, "big")
        want = [(scale(v, 1000),) * 3 for v in (0, 500, 1000)]
        self.assertEqual(parse(data)["images"][0]["crc32"], crc(want))

    def test_endianness(self):  # bytes 01 00 mean sample 256, not 1
        data = b"P5\n1 1\n1000\n" + bytes([1, 0])
        self.assertEqual(parse(data)["images"][0]["crc32"],
                         crc([(scale(256, 1000),) * 3]))


class PamTests(unittest.TestCase):
    def test_rgb_alpha(self):
        pam = (b"P7\nWIDTH 2\nHEIGHT 1\nDEPTH 4\nMAXVAL 255\nTUPLTYPE RGB_ALPHA\nENDHDR\n"
               + bytes([10, 20, 30, 255, 40, 50, 60, 0]))
        img = parse(pam)["images"][0]
        self.assertEqual(img["format"], "P7")
        self.assertEqual((img["width"], img["height"], img["maxval"]), (2, 1, 255))
        self.assertTrue(img["alpha"])
        self.assertEqual(img["crc32"], crc([(10, 20, 30), (40, 50, 60)]))
        self.assertEqual(img["colours"], 2)

    def test_grayscale_inferred_tupltype(self):
        pam = b"P7\nWIDTH 2\nHEIGHT 1\nDEPTH 1\nMAXVAL 255\nENDHDR\n" + bytes([10, 200])
        img = parse(pam)["images"][0]
        self.assertFalse(img["alpha"])
        self.assertEqual(img["crc32"], crc([(10, 10, 10), (200, 200, 200)]))

    def test_blackandwhite_bit_semantics(self):
        pam = (b"P7\nWIDTH 3\nHEIGHT 1\nDEPTH 1\nMAXVAL 1\n"
               b"TUPLTYPE BLACKANDWHITE\nENDHDR\n" + bytes([1, 0, 1]))
        self.assertEqual(parse(pam)["images"][0]["crc32"],
                         crc([(0, 0, 0), (255, 255, 255), (0, 0, 0)]))

    def test_blackandwhite_alpha(self):
        pam = (b"P7\nWIDTH 2\nHEIGHT 1\nDEPTH 2\nMAXVAL 1\n"
               b"TUPLTYPE BLACKANDWHITE_ALPHA\nENDHDR\n" + bytes([1, 255, 0, 255]))
        img = parse(pam)["images"][0]
        self.assertTrue(img["alpha"])
        self.assertEqual(img["crc32"], crc([(0, 0, 0), (255, 255, 255)]))

    def test_grayscale_alpha_scaled(self):
        pam = (b"P7\nWIDTH 1\nHEIGHT 1\nDEPTH 2\nMAXVAL 100\n"
               b"TUPLTYPE GRAYSCALE_ALPHA\nENDHDR\n" + bytes([50, 100]))
        img = parse(pam)["images"][0]
        self.assertTrue(img["alpha"])
        self.assertEqual(img["crc32"], crc([(scale(50, 100),) * 3]))

    def test_rgb_16bit(self):
        pam = (b"P7\nWIDTH 1\nHEIGHT 1\nDEPTH 3\nMAXVAL 4095\nTUPLTYPE RGB\nENDHDR\n"
               + (4095).to_bytes(2, "big") + (0).to_bytes(2, "big")
               + (2048).to_bytes(2, "big"))
        want = (scale(4095, 4095), scale(0, 4095), scale(2048, 4095))
        self.assertEqual(parse(pam)["images"][0]["crc32"], crc([want]))

    def test_missing_required_field(self):
        with self.assertRaises(PnmError):
            parse(b"P7\nWIDTH 1\nENDHDR\n")


class StreamTests(unittest.TestCase):
    def test_multiple_images(self):
        data = (b"P6\n1 1\n255\n" + bytes([1, 2, 3])
                + b"\nP5\n1 1\n255\n" + bytes([9]))
        images = parse(data)["images"]
        self.assertEqual(len(images), 2)
        self.assertEqual(images[0]["crc32"], crc([(1, 2, 3)]))
        self.assertEqual(images[1]["crc32"], crc([(9, 9, 9)]))

    def test_bad_maxval_and_truncation(self):
        for bad in (b"P6\n1 1\n0\n", b"P4\n1 1\n"):
            with self.assertRaises(PnmError):
                parse(bad)
        with self.assertRaises(PnmError):
            parse(b"P5\n2 2\n255\n" + bytes([1, 2, 3]))


if __name__ == "__main__":
    unittest.main()
