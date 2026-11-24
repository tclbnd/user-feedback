import unittest

from tgakit.tga import parse

from images import palette, rgb555, rle_gray, tga2, tiny


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(tiny())
        self.assertEqual((out["type"], out["width"], out["height"], out["depth"]), ("truecolor", 2, 1, 24))

    def test_pixels(self):
        out = parse(tiny())
        self.assertEqual((out["pixels"], out["complete"]), (2, True))

    def test_version_1_without_footer(self):
        self.assertEqual(parse(tiny())["version"], 1)

    def test_rle_packet_count_is_stored_minus_one(self):
        out = parse(rle_gray())
        self.assertEqual((out["pixels"], out["complete"], out["rle_packets"]), (3, True, 1))

    def test_grayscale_corner(self):
        self.assertEqual(parse(rle_gray())["corner_pixel"], "#070707ff")

    def test_image_id_is_read_and_skipped(self):
        out = parse(palette())
        self.assertEqual(out["image_id"], "tile")
        self.assertTrue(out["complete"])

    def test_colormapped_corner_goes_through_the_map(self):
        out = parse(palette())
        self.assertEqual((out["origin"], out["corner_pixel"]), ("top_left", "#ff0000ff"))

    def test_16bit_pixel_is_widened_from_5_bits(self):
        out = parse(rgb555())
        self.assertEqual((out["alpha_bits"], out["corner_pixel"]), (1, "#ff0000ff"))

    def test_tga2_footer_and_extension(self):
        out = parse(tga2())
        self.assertEqual(out["version"], 2)
        ext = out["extension"]
        self.assertEqual(ext["author"], "Jane Doe")
        self.assertEqual(ext["comments"], "first line\nlast line")
        self.assertEqual(ext["timestamp"], "2025-03-04T05:06:07")
        self.assertEqual(ext["job"], "shot 12")
        self.assertEqual(ext["software"], "PaintBox")
        self.assertEqual(ext["software_version"], "4.10b")
        self.assertEqual(ext["pixel_aspect"], "4/3")
        self.assertEqual(ext["gamma"], "11/5")
        self.assertEqual(ext["attribute_type"], "alpha")
        self.assertEqual(ext["thumbnail"], [8, 8])


if __name__ == "__main__":
    unittest.main()
