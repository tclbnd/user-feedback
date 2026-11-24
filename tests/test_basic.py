import unittest

from exrkit.exr import parse

import headers
from headers import minimal


class BasicTests(unittest.TestCase):
    def test_version(self):
        out = parse(minimal())
        self.assertEqual((out["version"], out["multipart"], len(out["parts"])), (2, False, 1))

    def test_compression(self):
        part = parse(minimal())["parts"][0]
        self.assertEqual((part["compression"], part["line_order"]), ("rle", "increasing_y"))

    def test_pixel_types(self):
        body = headers.attr("channels", "chlist", headers.chlist(("R", 0, 1, 1), ("G", 1, 1, 1), ("B", 2, 1, 1)))
        part = parse(headers.file(2, headers.part(body)))["parts"][0]
        self.assertEqual([(c["name"], c["type"]) for c in part["channels"]],
                         [("R", "uint"), ("G", "half"), ("B", "float")])

    def test_box_is_inclusive(self):
        part = parse(headers.file(2, headers.scanline(window=(0, 0, 99, 49))))["parts"][0]
        self.assertEqual(part["data_window"], {"origin": [0, 0], "size": [100, 50]})

    def test_dwa_compression(self):
        names = [parse(headers.file(2, headers.scanline(compression=i)))["parts"][0]["compression"]
                 for i in (8, 9)]
        self.assertEqual(names, ["dwaa", "dwab"])


class VersionFlagTests(unittest.TestCase):
    def tiled_file(self):
        return parse(headers.file(0x202, headers.tiled("beauty", "tiledimage", (0, 0, 63, 63),
                                                       headers.tiledesc(32, 32, 0, 0))))

    def test_tiled_flag(self):
        self.assertTrue(self.tiled_file()["tiled"])

    def test_tiled_flag_sets_the_type(self):
        body = headers.attr("channels", "chlist", headers.chlist(("R", 1, 1, 1)))
        body += headers.attr("tiles", "tiledesc", headers.tiledesc(32, 32, 0, 0))
        out = parse(headers.file(0x202, headers.part(body)))
        self.assertEqual(out["parts"][0]["type"], "tiledimage")

    def test_scanline_file_has_no_tiled_flag(self):
        self.assertFalse(parse(minimal())["tiled"])


class CustomAttributeTests(unittest.TestCase):
    def test_custom_names_sorted(self):
        part = parse(headers.file(2, headers.scanline(extra=[
            ("zOwner", "string", headers.string("plates")),
            ("aRate", "double", b"\0" * 8),
            ("matrix", "m44f", b"\0" * 64)])))["parts"][0]
        self.assertEqual(part["custom"], ["aRate", "matrix", "zOwner"])

    def test_known_names_are_not_custom(self):
        part = parse(headers.file(2, headers.scanline(extra=[
            ("screenWindowCenter", "v2f", b"\0" * 8),
            ("chunkCount", "int", b"\1\0\0\0")])))["parts"][0]
        self.assertEqual(part["custom"], [])

    def test_unknown_type_does_not_abort_the_parse(self):
        part = parse(headers.file(2, headers.scanline(extra=[
            ("deepThing", "someCustomType", b"whatever")])))["parts"][0]
        self.assertEqual((part["compression"], part["data_window"]["size"]), ("rle", [100, 50]))
        self.assertEqual(part["custom"], ["deepThing"])

    def test_unknown_type_keeps_the_other_attributes(self):
        part = parse(headers.file(2, headers.scanline(extra=[
            ("firstThing", "someCustomType", b"x"),
            ("secondThing", "anotherType", b"y")])))["parts"][0]
        self.assertEqual(part["custom"], ["firstThing", "secondThing"])


class FrameRateTests(unittest.TestCase):
    def frame_rate(self, num, den):
        return parse(headers.file(2, headers.scanline(extra=[
            ("framesPerSecond", "rational", headers.rational(num, den))])))["parts"][0]["frame_rate"]

    def test_fraction(self):
        self.assertEqual(self.frame_rate(24000, 1001), "24000/1001")

    def test_reduced(self):
        self.assertEqual(self.frame_rate(48, 2), "24")

    def test_absent(self):
        self.assertIsNone(parse(headers.file(2, headers.scanline()))["parts"][0]["frame_rate"])


class TileDescriptionTests(unittest.TestCase):
    def tiles(self, mode, rounding):
        return parse(headers.file(0x202, headers.tiled(
            "beauty", "tiledimage", (0, 0, 1023, 767),
            headers.tiledesc(64, 48, mode, rounding))))["parts"][0]["tiles"]

    def test_one_level(self):
        self.assertEqual(self.tiles(0, 0),
                         {"size": [64, 48], "level_mode": "one_level", "rounding": "down"})

    def test_mipmap(self):
        self.assertEqual(self.tiles(1, 1),
                         {"size": [64, 48], "level_mode": "mipmap", "rounding": "up"})

    def test_ripmap(self):
        self.assertEqual(self.tiles(2, 0),
                         {"size": [64, 48], "level_mode": "ripmap", "rounding": "down"})

    def test_scanline_has_no_tiles(self):
        self.assertIsNone(parse(headers.file(2, headers.scanline()))["parts"][0]["tiles"])


class ChunkTests(unittest.TestCase):
    def scanline_chunks(self, compression, height=100):
        return parse(headers.file(2, headers.scanline(
            compression=compression, window=(0, 0, 99, height - 1))))["parts"][0]["chunks"]

    def tiled_chunks(self, window, tiles):
        return parse(headers.file(0x202, headers.tiled(
            "beauty", "tiledimage", window, tiles)))["parts"][0]["chunks"]

    def test_scanline_blocks(self):
        # One line per block for none, rle and zips; 16 for zip and pxr24; 32 for
        # piz, b44, b44a and dwaa; 256 for dwab.
        self.assertEqual([self.scanline_chunks(i) for i in range(10)],
                         [100, 100, 100, 7, 4, 7, 4, 4, 4, 1])

    def test_scanline_partial_block(self):
        self.assertEqual([self.scanline_chunks(i, 17) for i in (1, 3, 6, 9)], [17, 2, 1, 1])

    def test_tiled_one_level(self):
        self.assertEqual(self.tiled_chunks((0, 0, 1023, 767), headers.tiledesc(64, 64, 0, 0)), 16 * 12)

    def test_tiled_mipmap(self):
        # 1024x768 and its halvings, down to 1x1: 192, 48, 12, 4 tiles and one
        # tile for each of the remaining levels.
        self.assertEqual(self.tiled_chunks((0, 0, 1023, 767), headers.tiledesc(64, 64, 1, 0)),
                         192 + 48 + 12 + 4 + 7)

    def test_tiled_ripmap(self):
        # Every x level against every y level: 9 x 9 combinations of a 256x256
        # window in 64x64 tiles.
        self.assertEqual(self.tiled_chunks((0, 0, 255, 255), headers.tiledesc(64, 64, 2, 0)), 13 * 13)

    def test_tiled_window_offset(self):
        # The tiles follow the window, not the origin: 1088 x 800 pixels.
        self.assertEqual(self.tiled_chunks((-64, -32, 1023, 767), headers.tiledesc(32, 32, 0, 0)), 34 * 25)

    def test_no_data_window(self):
        self.assertIsNone(parse(minimal())["parts"][0]["chunks"])


class MultipartTests(unittest.TestCase):
    def multipart(self):
        return parse(headers.file(
            0x1002,
            headers.scanline(compression=4, window=(0, 0, 1919, 1079), extra=[
                ("name", "string", headers.string("beauty")),
                ("type", "string", headers.string("scanlineimage"))]),
            headers.tiled("depth", "tiledimage", (0, 0, 1023, 767),
                          headers.tiledesc(64, 64, 1, 0), compression=9),
            headers.tiled("motion", "tiledimage", (0, 0, 255, 255),
                          headers.tiledesc(32, 32, 0, 0), compression=0)))

    def test_parts(self):
        out = self.multipart()
        self.assertTrue(out["multipart"])
        self.assertEqual([(p["name"], p["type"]) for p in out["parts"]],
                         [("beauty", "scanlineimage"), ("depth", "tiledimage"), ("motion", "tiledimage")])

    def test_parts_are_described_separately(self):
        parts = self.multipart()["parts"]
        self.assertEqual([(p["compression"], p["chunks"], p["tiles"] is not None) for p in parts],
                         [("piz", 34, False), ("dwab", 263, True), ("none", 64, True)])

    def test_multipart_type_is_not_taken_from_the_flags(self):
        body = headers.attr("name", "string", headers.string("beauty"))
        body += headers.attr("type", "string", headers.string("scanlineimage"))
        out = parse(headers.file(0x1202, headers.part(body)))
        self.assertEqual(out["parts"][0]["type"], "scanlineimage")

    def test_single_part_file_has_one_part(self):
        self.assertEqual(len(parse(minimal())["parts"]), 1)


if __name__ == "__main__":
    unittest.main()
