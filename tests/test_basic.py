import struct
import unittest

from exrkit.exr import parse

from headers import (attr, box2i, chlist, header, minimal, multipart, part,
                     rational, tiledesc)


class BasicTests(unittest.TestCase):
    def test_version(self):
        out = parse(minimal())
        self.assertEqual((out["version"], out["multipart"], len(out["parts"])), (2, False, 1))

    def test_compression(self):
        part = parse(minimal())["parts"][0]
        self.assertEqual((part["compression"], part["line_order"]), ("rle", "increasing_y"))


class VersionTests(unittest.TestCase):
    def test_tiled_flag_is_bit_nine(self):
        out = parse(header(version=2 | 0x200))
        self.assertTrue(out["tiled"])
        self.assertEqual(out["parts"][0]["type"], "tiledimage")

    def test_flags_do_not_overlap(self):
        out = parse(header(version=2 | 0x400))
        self.assertEqual((out["tiled"], out["long_names"]), (False, True))


class AttributeTests(unittest.TestCase):
    def test_pixel_types(self):
        value = chlist([("R", 1, 1, 1), ("Z", 2, 1, 1), ("A", 0, 2, 2)])
        out = parse(header(attr("channels", "chlist", value)))
        self.assertEqual([c["type"] for c in out["parts"][0]["channels"]], ["half", "float", "uint"])
        self.assertEqual(out["parts"][0]["channels"][2]["sampling"], [2, 2])

    def test_box2i_size_includes_both_ends(self):
        data = header(attr("dataWindow", "box2i", box2i(0, 0, 9, 4)),
                      attr("displayWindow", "box2i", box2i(-2, -3, 2, 2)))
        part = parse(data)["parts"][0]
        self.assertEqual(part["data_window"], {"origin": [0, 0], "size": [10, 5]})
        self.assertEqual(part["display_window"], {"origin": [-2, -3], "size": [5, 6]})

    def test_dwa_compressions(self):
        for value, name in ((b"\x08", "dwaa"), (b"\x09", "dwab")):
            part = parse(header(attr("compression", "compression", value)))["parts"][0]
            self.assertEqual(part["compression"], name)

    def test_unknown_type_is_kept_as_custom(self):
        data = header(attr("panel", "exrkit_custom", b"\x00\x01"))
        part = parse(data)["parts"][0]
        self.assertEqual(part["custom"], ["panel"])

    def test_custom_lists_only_undescribed_attributes(self):
        data = header(attr("compression", "compression", b"\x00"),
                      attr("screenWindowCenter", "v2f", struct.pack("<2f", 0, 0)),
                      attr("screenWindowWidth", "float", struct.pack("<f", 1)),
                      attr("chunkCount", "int", struct.pack("<i", 3)),
                      attr("version", "int", struct.pack("<i", 1)),
                      attr("owner", "string", b"vendor\0"),
                      attr("comment", "string", b"hi\0"))
        self.assertEqual(parse(data)["parts"][0]["custom"], ["comment", "owner"])

    def test_frame_rate(self):
        part = parse(header(attr("framesPerSecond", "rational", rational(24000, 1001))))["parts"][0]
        self.assertEqual(part["frame_rate"], "24000/1001")

    def test_frame_rate_absent(self):
        self.assertIsNone(parse(minimal())["parts"][0]["frame_rate"])


class TiledTests(unittest.TestCase):
    def test_tiledesc(self):
        data = header(attr("tiles", "tiledesc", tiledesc(128, 64, 2, 1)), version=2 | 0x200)
        part = parse(data)["parts"][0]
        self.assertEqual(part["tiles"],
                         {"size": [128, 64], "level_mode": "ripmap", "rounding": "up"})

    def test_no_tiles(self):
        self.assertIsNone(parse(minimal())["parts"][0]["tiles"])


class ChunkTests(unittest.TestCase):
    def scanline(self, value, height):
        return parse(header(attr("compression", "compression", value),
                            attr("dataWindow", "box2i", box2i(0, 0, 0, height - 1))))["parts"][0]["chunks"]

    def test_scanline_blocks_follow_compression(self):
        self.assertEqual(self.scanline(b"\x00", 100), 100)   # none
        self.assertEqual(self.scanline(b"\x01", 100), 100)   # rle
        self.assertEqual(self.scanline(b"\x02", 100), 100)   # zips
        self.assertEqual(self.scanline(b"\x03", 100), 7)     # zip, 16 lines
        self.assertEqual(self.scanline(b"\x04", 100), 4)     # piz, 32 lines
        self.assertEqual(self.scanline(b"\x08", 100), 4)     # dwaa, 32 lines
        self.assertEqual(self.scanline(b"\x09", 100), 1)     # dwab, 256 lines

    def tiled(self, level_mode, width, height, size=16):
        data = header(attr("tiles", "tiledesc", tiledesc(size, size, level_mode, 0)),
                      attr("dataWindow", "box2i", box2i(0, 0, width - 1, height - 1)),
                      version=2 | 0x200)
        return parse(data)["parts"][0]["chunks"]

    def test_one_level(self):
        self.assertEqual(self.tiled(0, 100, 100), 7 * 7)

    def test_tile_size_covers_partial_tiles(self):
        # 100x50 with 30x40 tiles -> ceil(100/30)=4 columns, ceil(50/40)=2 rows.
        data = header(attr("tiles", "tiledesc", tiledesc(30, 40, 0, 0)),
                      attr("dataWindow", "box2i", box2i(0, 0, 99, 49)),
                      version=2 | 0x200)
        self.assertEqual(parse(data)["parts"][0]["chunks"], 8)

    def test_uses_data_window_not_display_window(self):
        data = header(attr("compression", "compression", b"\x03"),
                      attr("dataWindow", "box2i", box2i(-64, -32, -64 + 99, -32 + 31)),
                      attr("displayWindow", "box2i", box2i(0, 0, 999, 999)))
        self.assertEqual(parse(data)["parts"][0]["chunks"], 2)

    def test_mipmap_uses_diagonal_levels(self):
        # Levels 100, 50, 25, 12, 6, 3, 1 -> 49+16+4+1+1+1+1
        self.assertEqual(self.tiled(1, 100, 100), 73)

    def test_ripmap_uses_each_level_pair(self):
        # Sizes 100, 50, 25, 12, 6, 3, 1 need 7, 4, 2, 1, 1, 1, 1 tiles per axis,
        # i.e. 17 per axis, and every level pair is present: 17 * 17.
        self.assertEqual(self.tiled(2, 100, 100), 289)

    def test_chunks_absent_without_window(self):
        self.assertIsNone(parse(minimal())["parts"][0]["chunks"])


class MultipartTests(unittest.TestCase):
    def test_each_part_is_described(self):
        data = multipart(
            part("beauty", "scanlineimage"),
            part("depth", "tiledimage", attr("tiles", "tiledesc", tiledesc(32, 32, 0, 0))),
        )
        out = parse(data)
        self.assertTrue(out["multipart"])
        self.assertEqual([p["name"] for p in out["parts"]], ["beauty", "depth"])
        self.assertEqual([p["type"] for p in out["parts"]], ["scanlineimage", "tiledimage"])
        self.assertEqual(out["parts"][1]["tiles"]["size"], [32, 32])

    def test_multipart_parts_have_own_compression(self):
        data = multipart(
            part("a", "scanlineimage", compression=b"\x00"),
            part("b", "scanlineimage", compression=b"\x09"),
        )
        parts = parse(data)["parts"]
        self.assertEqual([p["compression"] for p in parts], ["none", "dwab"])
        self.assertEqual([p["chunks"] for p in parts], [10, 1])

    def test_part_without_type_stays_null(self):
        data = multipart(attr("name", "string", b"mystery\0") + b"\0")
        self.assertIsNone(parse(data)["parts"][0]["type"])
