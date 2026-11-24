import struct
import unittest

from anikit.ani import parse


def minimal():
    anih = b"anih" + struct.pack("<I", 36) + struct.pack("<9I", 36, 0, 0, 0, 0, 0, 0, 6, 1)
    return b"RIFF" + struct.pack("<I", 4 + len(anih)) + b"ACON" + anih


def riff(chunks_):
    body = b"ACON" + b"".join(chunks_)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def chunk(cid, body):
    return cid + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) & 1 else b"")


def anih(nframes, nsteps, rate=6, attrs=1):
    return chunk(b"anih", struct.pack("<9I", 36, nframes, nsteps, 0, 0, 0, 0, rate, attrs))


def bmp_cursor(width, height, bpp, hx, hy):
    info = struct.pack("<IiiHHIIiiII", 40, width, height * 2, 1, bpp, 0, 0, 0, 0, 0, 0)
    entry = struct.pack("<BBBBHHII", width, height, 0, 0, hx, hy, len(info), 22)
    return struct.pack("<HHH", 0, 2, 1) + entry + info


def png_cursor(width, height, bit_depth, color_type, hx, hy):
    ihdr = (struct.pack(">I", 13) + b"IHDR"
            + struct.pack(">II", width, height) + bytes([bit_depth, color_type, 0, 0, 0]))
    img = b"\x89PNG\r\n\x1a\n" + ihdr
    entry = struct.pack("<BBBBHHII",
                        0 if width == 256 else width,
                        0 if height == 256 else height,
                        0, 0, hx, hy, len(img), 22)
    return struct.pack("<HHH", 0, 2, 1) + entry + img


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(minimal())
        self.assertEqual((out["frames"], out["default_rate"], out["icon_frames"]), (0, 6, True))

    def test_no_frames(self):
        self.assertEqual(parse(minimal())["frame_images"], [])


class FormatTests(unittest.TestCase):
    def test_loop_uses_jiffies_and_rate_chunk(self):
        # Per-step rates 30+6+6+6+30 = 78 jiffies -> 78/60 = 13/10 s.
        data = riff([
            anih(3, 5, rate=10),
            chunk(b"rate", struct.pack("<5I", 30, 6, 6, 6, 30)),
            chunk(b"seq ", struct.pack("<5I", 0, 1, 2, 1, 0)),
        ])
        out = parse(data)
        self.assertEqual(out["sequence"], [0, 1, 2, 1, 0])
        self.assertEqual(out["loop_seconds"], "13/10")

    def test_default_rate_is_in_jiffies(self):
        # Two steps at 30 jiffies each -> 60/60 = 1 s.
        out = parse(riff([anih(2, 2, rate=30)]))
        self.assertEqual(out["sequence"], [0, 1])
        self.assertEqual(out["loop_seconds"], "1")

    def test_odd_sized_chunks_keep_all_frames(self):
        odd_body = b"abc"  # junk chunk with a padding byte
        fram = chunk(b"LIST", b"fram"
                     + chunk(b"icon", bmp_cursor(32, 32, 32, 16, 16))
                     + chunk(b"icon", bmp_cursor(32, 32, 32, 1, 2))
                     + chunk(b"icon", bmp_cursor(32, 32, 32, 3, 4)))
        out = parse(riff([anih(3, 3), chunk(b"junk", odd_body), fram]))
        self.assertEqual([f["hotspot"] for f in out["frame_images"]],
                         [[16, 16], [1, 2], [3, 4]])

    def test_info_list_title_and_artist(self):
        info = chunk(b"LIST", b"INFO"
                     + chunk(b"INAM", b"Busy Ring\x00")
                     + chunk(b"IART", b"Theme Studio\x00"))
        out = parse(riff([anih(0, 0), info]))
        self.assertEqual(out["title"], "Busy Ring")
        self.assertEqual(out["artist"], "Theme Studio")

    def test_bmp_cursor_frame(self):
        fram = chunk(b"LIST", b"fram" + chunk(b"icon", bmp_cursor(32, 32, 32, 7, 9)))
        out = parse(riff([anih(1, 1), fram]))
        img = out["frame_images"][0]
        self.assertEqual(img, {"type": "cursor", "width": 32, "height": 32,
                               "bit_count": 32, "png": False, "images": 1,
                               "hotspot": [7, 9]})

    def test_png_cursor_frame_256(self):
        fram = chunk(b"LIST", b"fram"
                     + chunk(b"icon", png_cursor(256, 256, 8, 6, 5, 11)))
        out = parse(riff([anih(1, 1), fram]))
        img = out["frame_images"][0]
        self.assertEqual(img["type"], "cursor")
        self.assertEqual((img["width"], img["height"]), (256, 256))
        self.assertTrue(img["png"])
        self.assertEqual(img["bit_count"], 32)  # depth 8 * RGBA 4 channels
        self.assertEqual(img["hotspot"], [5, 11])

    def test_multi_image_icon_resource(self):
        bmp1 = struct.pack("<IiiHHIIiiII", 40, 16, 32, 1, 24, 0, 0, 0, 0, 0, 0)
        bmp2 = struct.pack("<IiiHHIIiiII", 40, 32, 64, 1, 32, 0, 0, 0, 0, 0, 0)
        e1 = struct.pack("<BBBBHHII", 16, 16, 0, 0, 1, 24, len(bmp1), 38)
        e2 = struct.pack("<BBBBHHII", 32, 32, 0, 0, 1, 32, len(bmp2), 38 + len(bmp1))
        resource = struct.pack("<HHH", 0, 1, 2) + e1 + e2 + bmp1 + bmp2
        fram = chunk(b"LIST", b"fram" + chunk(b"icon", resource))
        out = parse(riff([anih(1, 1), fram]))
        img = out["frame_images"][0]
        self.assertEqual(img["type"], "icon")
        self.assertEqual(img["images"], 2)
        self.assertEqual((img["width"], img["height"], img["bit_count"]), (16, 16, 24))
        self.assertIsNone(img["hotspot"])
