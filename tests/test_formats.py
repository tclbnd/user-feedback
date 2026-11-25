import struct
import unittest

from ktxkit.texture import KtxError, parse

ID = b"\xabKTX 11\xbb\r\n\x1a\n"
UBYTE = 0x1401
RED, RG, RGB, BGR, RGBA, BGRA = 0x1903, 0x8227, 0x1907, 0x80E0, 0x1908, 0x80E1
LUM, LUMA, ALPHA_FMT = 0x1909, 0x190A, 0x1906
U565, U5551, U4444 = 0x8363, 0x8034, 0x8033
U565_REV, U4444_REV, U5551_REV = 0x8364, 0x8365, 0x8366


def build(endian, gltype, typesize, glformat, internal, w, h, d, arr, faces,
          payloads, kv=b""):
    """payloads: list of (concatenated image bytes, imageSize per image)."""
    head = ID + struct.pack(
        endian + "13I", 0x04030201, gltype, typesize, glformat, internal,
        glformat, w, h, d, arr, faces, len(payloads), len(kv))
    out = head + kv
    for payload, one_size in payloads:
        out += struct.pack(endian + "I", one_size) + payload
        out += b"\0" * ((-len(payload)) % 4)
    return out


def kv_entry(key, value):
    blob = key + b"\0" + value + b"\0"
    return struct.pack("<I", len(blob)) + blob + b"\0" * ((-len(blob)) % 4)


class UbyteLayoutTests(unittest.TestCase):
    def test_channel_arrangements(self):
        cases = [
            (RED, b"\x10", "#101010ff"),
            (RG, b"\x10\x20", "#102000ff"),
            (RGB, b"\x10\x20\x30", "#102030ff"),
            (BGR, b"\x10\x20\x30", "#302010ff"),
            (RGBA, b"\x10\x20\x30\x40", "#10203040"),
            (BGRA, b"\x10\x20\x30\x40", "#30201040"),
            (LUM, b"\x10", "#101010ff"),
            (LUMA, b"\x10\x40", "#10101040"),
            (ALPHA_FMT, b"\x40", "#00000040"),
        ]
        for glformat, raw, want in cases:
            with self.subTest(glformat=glformat):
                d = build("<", UBYTE, 1, glformat, glformat, 1, 1, 0, 0, 1,
                          [(raw, len(raw))])
                self.assertEqual(parse(d)["first_texels"], [want])

    def test_big_endian_file(self):
        d = build(">", UBYTE, 1, RGBA, RGBA, 1, 1, 0, 0, 1,
                  [(b"\x10\x20\x30\x40", 4)])
        out = parse(d)
        self.assertEqual(out["endianness"], "big")
        self.assertEqual(out["first_texels"], ["#10203040"])


class PackedTests(unittest.TestCase):
    def test_packed_layouts_both_endians(self):
        # (glType, glFormat, 16-bit word, expected "#rrggbbaa")
        cases = [
            (U565, RGB, 0x0843, "#080819ff"),     # R1 G2 B3
            (U5551, RGBA, 0xF801, "#ff0000ff"),   # R31 A1
            (U5551, RGBA, 0x0000, "#00000000"),   # A0 -> transparent
            (U4444, RGBA, 0xF008, "#ff000088"),   # R15 A8
            (U565_REV, RGB, 0x0843, "#190808ff"),
            (U4444_REV, RGBA, 0x4321, "#11223344"),
            (U5551_REV, RGBA, 0x8461, "#198484ff"),
        ]
        for gltype, glformat, word, want in cases:
            for endian in ("<", ">"):
                with self.subTest(gltype=gltype, endian=endian):
                    d = build(endian, gltype, 2, glformat, gltype, 1, 1, 0,
                              0, 1, [(struct.pack(endian + "H", word), 2)])
                    self.assertEqual(parse(d)["first_texels"], [want])

    def test_component_scaling(self):
        from ktxkit.texture import _scale
        for value, mx, want in [(0, 31, 0), (31, 31, 255), (1, 31, 8),
                                (8, 31, 66), (15, 31, 123), (16, 31, 132),
                                (1, 15, 17), (15, 15, 255), (1, 1, 255)]:
            self.assertEqual(_scale(value, mx), want)


class StructureTests(unittest.TestCase):
    def test_mip_chain(self):
        l0 = bytes(range(16)) * 4
        l1 = bytes(range(40, 56))
        d = build("<", UBYTE, 1, RGBA, RGBA, 4, 4, 0, 0, 1,
                  [(l0, 64), (l1, 16)])
        out = parse(d)
        self.assertEqual(out["levels"], [[4, 4, 64], [2, 2, 16]])
        self.assertEqual(out["mip_levels"], 2)
        self.assertTrue(out["complete"])
        self.assertEqual(out["smallest_texel"], "#28292a2b")
        self.assertEqual(out["last_texel"], "#0c0d0e0f")

    def test_array(self):
        imgs = [bytes(i * 16 + j for j in range(16)) for i in range(3)]
        d = build("<", UBYTE, 1, RGBA, RGBA, 2, 2, 0, 3, 1,
                  [(b"".join(imgs), 16)])
        out = parse(d)
        self.assertEqual(out["array_elements"], 3)
        self.assertEqual(out["first_texels"],
                         ["#00010203", "#10111213", "#20212223"])
        self.assertEqual(out["last_texel"], "#2c2d2e2f")

    def test_3d_texture(self):
        s0, s1 = bytes(range(16)), bytes(range(20, 36))
        d = build("<", UBYTE, 1, RGBA, RGBA, 2, 2, 2, 0, 1, [(s0 + s1, 16)])
        out = parse(d)
        self.assertEqual(out["depth"], 2)
        self.assertEqual(out["first_texels"], ["#00010203", "#14151617"])
        self.assertEqual(out["last_texel"], "#20212223")

    def test_cubemap_big_endian(self):
        faces = [bytes((i + 1) * 16 + j for j in range(16)) for i in range(6)]
        d = build(">", UBYTE, 1, RGBA, RGBA, 2, 2, 0, 0, 6,
                  [(b"".join(faces), 16)])
        out = parse(d)
        self.assertEqual(out["endianness"], "big")
        self.assertEqual(out["faces"], 6)
        self.assertEqual(out["first_texels"],
                         ["#10111213", "#20212223", "#30313233",
                          "#40414243", "#50515253", "#60616263"])
        self.assertEqual(out["last_texel"], "#6c6d6e6f")
        self.assertTrue(out["complete"])

    def test_key_values(self):
        kv = kv_entry(b"KTXorientation", b"S=r,T=d") + kv_entry(b"Author",
                                                                 b"pipeline")
        d = build("<", UBYTE, 1, RGBA, RGBA, 1, 1, 0, 0, 1,
                  [(b"\x01\x02\x03\x04", 4)], kv=kv)
        out = parse(d)
        self.assertEqual(out["key_values"],
                         {"KTXorientation": "S=r,T=d", "Author": "pipeline"})
        self.assertEqual(out["first_texels"], ["#01020304"])

    def test_truncated_level_is_incomplete_but_partially_readable(self):
        l0 = bytes(range(16)) * 4
        d = build("<", UBYTE, 1, RGBA, RGBA, 4, 4, 0, 0, 1,
                  [(l0, 64), (bytes(range(4)), 16)])
        out = parse(d)
        self.assertFalse(out["complete"])
        # the first texel of the truncated second level is still present
        self.assertEqual(out["smallest_texel"], "#00010203")

    def test_level_without_data_has_null_texel(self):
        head = ID + struct.pack("<13I", 0x04030201, UBYTE, 1, RGBA, RGBA,
                                RGBA, 4, 4, 0, 0, 1, 2, 0)
        d = head + struct.pack("<I", 64) + bytes(range(16)) * 4
        d += struct.pack("<I", 16)  # level-1 size, no payload
        out = parse(d)
        self.assertFalse(out["complete"])
        self.assertEqual(out["mip_levels"], 2)
        self.assertIsNone(out["smallest_texel"])

    def test_truncated_payload_does_not_run_away(self):
        # header advertises 3 levels, but level 0 payload is cut short.
        head = ID + struct.pack("<13I", 0x04030201, UBYTE, 1, RGBA, RGBA,
                                RGBA, 4, 4, 0, 0, 1, 3, 0)
        d = head + struct.pack("<I", 64) + bytes(range(8))
        out = parse(d)
        self.assertFalse(out["complete"])
        self.assertEqual(out["mip_levels"], 1)
        self.assertIsNone(out["last_texel"])

    def test_compressed_texture_texels_are_null(self):
        d = build("<", 0, 1, 0, 0x83F1, 4, 4, 0, 0, 1, [(b"\0" * 16, 16)])
        out = parse(d)
        self.assertEqual(out["first_texels"], [])
        self.assertIsNone(out["last_texel"])
        self.assertIsNone(out["smallest_texel"])
        self.assertTrue(out["complete"])
        self.assertEqual(out["levels"], [[4, 4, 16]])

    def test_bad_endian_mark(self):
        with self.assertRaises(KtxError):
            parse(ID + b"\x99\x99\x99\x99" + b"\0" * 48)

    def test_first_texels_capped_at_eight_in_file_order(self):
        # 1x1 RGBA array with 10 elements: only the first 8 texels are shown.
        imgs = [bytes((i, i + 1, i + 2, i + 3)) for i in range(10)]
        d = build("<", UBYTE, 1, RGBA, RGBA, 1, 1, 0, 10, 1,
                  [(b"".join(imgs), 4)])
        out = parse(d)
        self.assertEqual(len(out["first_texels"]), 8)
        self.assertEqual(out["first_texels"][0], "#00010203")
        self.assertEqual(out["first_texels"][7], "#0708090a")
        # last texel is from the tenth image
        self.assertEqual(out["last_texel"], "#090a0b0c")

    def test_cube_array(self):
        # 2 array elements x 6 faces, each a 1x1 RGBA image.
        imgs = [bytes((i, i, i, 255)) for i in range(12)]
        d = build("<", UBYTE, 1, RGBA, RGBA, 1, 1, 0, 2, 6,
                  [(b"".join(imgs), 4)])
        out = parse(d)
        self.assertEqual(out["array_elements"], 2)
        self.assertEqual(out["faces"], 6)
        self.assertEqual(len(out["first_texels"]), 8)
        self.assertEqual(out["last_texel"], "#0b0b0bff")
        self.assertTrue(out["complete"])

    def test_1d_texture(self):
        # height/depth 0: a 4-texel-wide 1D RGBA texture.
        row = bytes(range(16))
        d = build("<", UBYTE, 1, RGBA, RGBA, 4, 0, 0, 0, 1, [(row, 16)])
        out = parse(d)
        self.assertEqual(out["levels"], [[4, 1, 16]])
        self.assertEqual(out["first_texels"], ["#00010203"])
        self.assertEqual(out["last_texel"], "#0c0d0e0f")

    def test_mip_count_zero_means_one_level(self):
        head = ID + struct.pack("<13I", 0x04030201, UBYTE, 1, RGBA, RGBA,
                                RGBA, 1, 1, 0, 0, 1, 0, 0)
        d = head + struct.pack("<I", 4) + b"\x01\x02\x03\x04"
        out = parse(d)
        self.assertEqual(out["mip_levels"], 1)
        self.assertEqual(out["levels"], [[1, 1, 4]])
        self.assertTrue(out["complete"])


if __name__ == "__main__":
    unittest.main()
