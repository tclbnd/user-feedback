import unittest

from webpkit.webp import WebpError, parse

from files import anim, animated, chunk, extended, frame, riff, still, vp8, vp8l


class BasicTests(unittest.TestCase):
    def test_still(self):
        out = parse(still(64, 48))
        self.assertEqual((out["image"]["codec"], out["image"]["width"], out["image"]["height"]), ("lossy", 64, 48))

    def test_not_animated(self):
        out = parse(still())
        self.assertEqual((out["animated"], out["frames"], out["canvas"]), (False, [], None))

    def test_still_lossless_alpha_from_bitstream(self):
        out = parse(riff(chunk(b"VP8L", vp8l(33, 17, alpha=True))))
        self.assertEqual((out["image"]["codec"], out["image"]["width"], out["image"]["height"], out["image"]["alpha"]),
                         ("lossless", 33, 17, True))

    def test_still_alpha_from_alph_chunk(self):
        out = parse(riff(chunk(b"ALPH", b"\0\0\0"), chunk(b"VP8 ", vp8(10, 12))))
        self.assertEqual((out["image"]["width"], out["image"]["height"], out["image"]["alpha"]), (10, 12, True))

    def test_not_webp(self):
        with self.assertRaises(WebpError):
            parse(b"RIFF\0\0\0\0WAVE")


class CanvasTests(unittest.TestCase):
    def test_canvas_is_stored_minus_one(self):
        out = parse(animated(canvas=(320, 240)))
        self.assertEqual(out["canvas"], {"width": 320, "height": 240})

    def test_one_pixel_canvas(self):
        out = parse(animated(canvas=(1, 1)))
        self.assertEqual(out["canvas"], {"width": 1, "height": 1})

    def test_feature_flags(self):
        out = parse(riff(extended(8, 8, 0x20 | 0x08 | 0x02), anim(),
                         frame(0, 0, 8, 8, 10, False, False, chunk(b"VP8 ", vp8(8, 8)))))
        self.assertEqual((out["icc"], out["exif"], out["xmp"], out["alpha"], out["animated"]),
                         (True, True, False, False, True))


class AnimationTests(unittest.TestCase):
    def test_background_is_bgra(self):
        out = parse(animated(bg=(0x99, 0x66, 0x33, 0x80)))
        self.assertEqual(out["background"], "#336699")
        self.assertEqual(out["background_alpha"], 0x80)
        self.assertEqual(out["loop_count"], 0)

    def test_background_red_only(self):
        out = parse(animated(bg=(0x00, 0x00, 0xff, 0xff)))
        self.assertEqual(out["background"], "#ff0000")

    def test_frame_position_is_doubled(self):
        out = parse(animated(frames=[frame(80, 24, 160, 40, 100, False, False, chunk(b"VP8 ", vp8(160, 40)))]))
        f = out["frames"][0]
        self.assertEqual((f["x"], f["y"], f["width"], f["height"]), (80, 24, 160, 40))

    def test_frame_at_origin(self):
        out = parse(animated(frames=[frame(0, 0, 320, 240, 100, False, False, chunk(b"VP8 ", vp8(320, 240)))]))
        f = out["frames"][0]
        self.assertEqual((f["x"], f["y"]), (0, 0))

    def test_frame_flags(self):
        # Bit 1 is the blending method (set = do not blend); bit 0 is disposal (set = clear).
        # flags: 0 = blend, keep; 1 = blend, clear; 2 = no blend, keep; 3 = no blend, clear.
        expected = {0: (True, False), 1: (True, True), 2: (False, False), 3: (False, True)}
        for flags, want in expected.items():
            hdr = b"\0\0\0" + b"\0\0\0" + b"\x07\0\0" + b"\x07\0\0" + b"\x64\0\0" + bytes([flags])
            out = parse(animated(frames=[chunk(b"ANMF", hdr + chunk(b"VP8 ", vp8(8, 8)))]))
            f = out["frames"][0]
            self.assertEqual((f["blend"], f["dispose"]), want, flags)

    def test_frame_helper_flags(self):
        # frame() takes the semantic values, so both orders round-trip.
        for blend in (False, True):
            for dispose in (False, True):
                out = parse(animated(frames=[frame(0, 0, 8, 8, 10, blend, dispose, chunk(b"VP8 ", vp8(8, 8)))]))
                f = out["frames"][0]
                self.assertEqual((f["blend"], f["dispose"]), (blend, dispose), (blend, dispose))

    def test_raw_flag_byte(self):
        # flags = 0x03: blending discarded, dispose to background.
        hdr = b"\0\0\0" + b"\0\0\0" + b"\x07\0\0" + b"\x07\0\0" + b"\x64\0\0" + b"\x03"
        out = parse(animated(frames=[chunk(b"ANMF", hdr + chunk(b"VP8 ", vp8(8, 8)))]))
        self.assertEqual((out["frames"][0]["blend"], out["frames"][0]["dispose"]), (False, True))

    def test_duration_is_sum(self):
        frames = [frame(0, 0, 8, 8, d, False, False, chunk(b"VP8 ", vp8(8, 8))) for d in (30, 40, 50)]
        out = parse(animated(frames=frames))
        self.assertEqual(out["duration"], 120)
        self.assertEqual([f["duration"] for f in out["frames"]], [30, 40, 50])

    def test_loop_count(self):
        out = parse(animated(loops=7))
        self.assertEqual(out["loop_count"], 7)

    def test_frame_without_image_data(self):
        out = parse(animated(frames=[frame(0, 0, 8, 8, 10)]))
        f = out["frames"][0]
        self.assertEqual((f["codec"], f["alpha"]), (None, False))

    def test_frame_lossless_alpha(self):
        out = parse(animated(frames=[frame(0, 0, 8, 8, 10, False, False, chunk(b"VP8L", vp8l(8, 8, alpha=True)))]))
        f = out["frames"][0]
        self.assertEqual((f["codec"], f["alpha"]), ("lossless", True))

    def test_frame_alpha_chunk(self):
        out = parse(animated(frames=[frame(0, 0, 8, 8, 10, False, False, chunk(b"ALPH", b"\0\0\0"),
                                               chunk(b"VP8 ", vp8(8, 8)))]))
        f = out["frames"][0]
        self.assertEqual((f["codec"], f["alpha"]), ("lossy", True))

    def test_odd_chunks_do_not_swallow_later_frames(self):
        frames = [frame(0, 0, 8, 8, 10, False, False, chunk(b"VP8L", vp8l(8, 8))) for _ in range(3)]
        out = parse(animated(features=0x22, frames=frames))
        self.assertEqual(len(out["frames"]), 3)
        self.assertEqual(out["duration"], 30)

    def test_metadata_after_frames(self):
        out = parse(animated(frames=[frame(0, 0, 8, 8, 10, False, False, chunk(b"VP8 ", vp8(8, 8)))],
                             extra=[chunk(b"EXIF", b"x"), chunk(b"XMP ", b"y")]))
        self.assertEqual(len(out["frames"]), 1)

    def test_odd_alph_chunk_in_frame(self):
        out = parse(animated(features=0x12,
                             frames=[frame(0, 0, 8, 8, 10, False, False, chunk(b"ALPH", b"\0\0\0"),
                                           chunk(b"VP8 ", vp8(8, 8)))]))
        f = out["frames"][0]
        self.assertEqual((f["codec"], f["alpha"], f["width"], f["height"]), ("lossy", True, 8, 8))


if __name__ == "__main__":
    unittest.main()
