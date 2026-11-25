import struct
import unittest

from exrkit.exr import parse

from headers import MAGIC, attr, box2i, chlist, file, minimal, tiledesc


class BasicTests(unittest.TestCase):
    def test_version(self):
        out = parse(minimal())
        self.assertEqual((out["version"], out["multipart"], len(out["parts"])), (2, False, 1))

    def test_compression(self):
        part = parse(minimal())["parts"][0]
        self.assertEqual((part["compression"], part["line_order"]), ("rle", "increasing_y"))


class HeaderTests(unittest.TestCase):
    def test_pixel_types(self):
        a = attr("channels", "chlist", chlist(("Z", 2), ("id", 0), ("R", 1)))
        part = parse(file(2, a))["parts"][0]
        got = [(c["name"], c["type"]) for c in part["channels"]]
        self.assertEqual(got, [("Z", "float"), ("id", "uint"), ("R", "half")])

    def test_box_size_is_inclusive(self):
        a = attr("dataWindow", "box2i", box2i(-64, -32, 1983, 825))
        part = parse(file(2, a))["parts"][0]
        self.assertEqual(part["data_window"], {"origin": [-64, -32], "size": [2048, 858]})

    def test_tiled_flag(self):
        out = parse(file(2 | 0x200, b""))
        self.assertTrue(out["tiled"])
        self.assertEqual(out["parts"][0]["type"], "tiledimage")

    def test_dwaa_compression(self):
        a = attr("compression", "compression", b"\x08")
        a += attr("dataWindow", "box2i", box2i(0, 0, 1919, 2159))
        part = parse(file(2, a))["parts"][0]
        self.assertEqual(part["compression"], "dwaa")
        self.assertEqual(part["chunks"], -(-2160 // 32))

    def test_unknown_attribute_type_is_kept(self):
        a = attr("preview", "preview", b"\x00" * 8)
        a += attr("owner", "string", b"studio")
        part = parse(file(2, a))["parts"][0]
        self.assertEqual(part["custom"], ["owner", "preview"])

    def test_tiles(self):
        a = attr("tiles", "tiledesc", tiledesc(128, 64, 2, 1))
        part = parse(file(2 | 0x200, a))["parts"][0]
        self.assertEqual(part["tiles"], {"size": [128, 64], "level_mode": "ripmap", "rounding": "up"})

    def test_scanline_chunks_per_compression(self):
        for comp, lines in ((0, 1), (3, 16), (4, 32), (9, 256)):
            a = attr("compression", "compression", bytes([comp]))
            a += attr("dataWindow", "box2i", box2i(0, 0, 99, 2159))
            part = parse(file(2, a))["parts"][0]
            self.assertEqual(part["chunks"], -(-2160 // lines), comp)

    def test_tiled_chunks_one_level(self):
        a = attr("tiles", "tiledesc", tiledesc(128, 128, 0, 0))
        a += attr("dataWindow", "box2i", box2i(0, 0, 2047, 857))
        part = parse(file(2 | 0x200, a))["parts"][0]
        self.assertEqual(part["chunks"], 16 * 7)

    def test_tiled_chunks_mipmap(self):
        a = attr("tiles", "tiledesc", tiledesc(128, 128, 1, 0))
        a += attr("dataWindow", "box2i", box2i(0, 0, 2047, 857))
        part = parse(file(2 | 0x200, a))["parts"][0]
        # 12 levels (2048 -> 1); 16*7 + 8*4 + 4*2 + 2*1 + 1*1 * 8
        self.assertEqual(part["chunks"], 112 + 32 + 8 + 2 + 8)

    def test_tiled_chunks_ripmap_round_up(self):
        a = attr("tiles", "tiledesc", tiledesc(64, 64, 2, 1))
        a += attr("dataWindow", "box2i", box2i(0, 0, 99, 199))
        part = parse(file(2 | 0x200, a))["parts"][0]
        # x levels: 100, 50, 25, 13, 7, 4, 2, 1 -> 2+1+1+1+1+1+1+1 tiles
        # y levels: 200, 100, 50, 25, 13, 7, 4, 2, 1 -> 4+2+1+1+1+1+1+1+1 tiles
        self.assertEqual(part["chunks"], 9 * 13)

    def test_frame_rate(self):
        a = attr("framesPerSecond", "rational", struct.pack("<2i", 24, 1))
        part = parse(file(2, a))["parts"][0]
        self.assertEqual(part["frame_rate"], "24/1")
        a = attr("framesPerSecond", "rational", struct.pack("<2i", 24000, 1001))
        part = parse(file(2, a))["parts"][0]
        self.assertEqual(part["frame_rate"], "24000/1001")

    def test_multipart(self):
        a = attr("name", "string", b"rgba")
        a += attr("type", "string", b"scanlineimage")
        a += attr("compression", "compression", b"\x03")
        a += attr("dataWindow", "box2i", box2i(0, 0, 639, 479))
        b = attr("name", "string", b"depth")
        b += attr("type", "string", b"tiledimage")
        b += attr("tiles", "tiledesc", tiledesc(16, 16, 0, 0))
        b += attr("dataWindow", "box2i", box2i(0, 0, 639, 479))
        out = parse(file(2 | 0x1000, a, b))
        self.assertTrue(out["multipart"])
        self.assertEqual(len(out["parts"]), 2)
        self.assertEqual(out["parts"][0]["name"], "rgba")
        self.assertEqual(out["parts"][0]["chunks"], 480 // 16)
        self.assertEqual(out["parts"][1]["name"], "depth")
        self.assertEqual(out["parts"][1]["chunks"], 40 * 30)

    def test_custom_sorted(self):
        a = attr("capDate", "string", b"2026:11:20 10:00:00")
        a += attr("owner", "string", b"studio")
        a += attr("framesPerSecond", "rational", struct.pack("<2i", 24, 1))
        part = parse(file(2, a))["parts"][0]
        self.assertEqual(part["custom"], ["capDate", "owner"])
