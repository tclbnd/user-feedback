import unittest

from supkit.sup import parse

from streams import clear, end, fragmented_ods, pcs, seg, subtitle


class BasicTests(unittest.TestCase):
    def test_video_size(self):
        out = parse(subtitle(0))
        self.assertEqual((out["width"], out["height"], out["display_sets"]), (1920, 1080, 1))

    def test_object(self):
        obj = parse(subtitle(0, w=100, h=2))["subtitles"][0]["objects"][0]
        self.assertEqual((obj["width"], obj["height"], obj["opaque_pixels"]), (100, 2, 100))

    def test_pts_is_90khz(self):
        subs = parse(subtitle(90000))["subtitles"]
        self.assertEqual((subs[0]["start"], subs[0]["end"]), ("1", None))

    def test_empty_display_set_ends_subtitle(self):
        subs = parse(subtitle(90000) + clear(450000))["subtitles"]
        self.assertEqual(len(subs), 1)
        self.assertEqual((subs[0]["start"], subs[0]["end"]), ("1", "5"))

    def test_palette_update_is_not_a_new_subtitle(self):
        fade = pcs([(0, 0, 10, 20, None)], pts=180000, state=0, palette_update=0x80) + end(180000)
        subs = parse(subtitle(90000) + fade + clear(270000))["subtitles"]
        self.assertEqual(len(subs), 1)
        self.assertEqual((subs[0]["end"], subs[0]["palette_updates"]), ("3", 1))

    def test_fragmented_object(self):
        picture = (bytes([0, 0x80 | 50, 1]) + bytes([0, 50]) + b"\x00\x00") * 4
        stream = pcs([(0, 0, 10, 20, None)]) + seg(0x14, bytes([0, 0, 1, 235, 128, 128, 255])) \
            + fragmented_ods(0, 100, 4, picture, 11) + end()
        obj = parse(stream)["subtitles"][0]["objects"][0]
        self.assertEqual((obj["width"], obj["height"], obj["opaque_pixels"]), (100, 4, 200))

    def test_cropped_composition_object(self):
        comps = [(0, 0x80, 10, 20, (0, 0, 100, 2)), (1, 0x40, 30, 40, None)]
        picture = (bytes([0, 0x80 | 50, 1]) + bytes([0, 50]) + b"\x00\x00") * 2
        ods = fragmented_ods(0, 100, 2, picture, 7) + fragmented_ods(1, 100, 2, picture, 7)
        stream = pcs(comps) + seg(0x14, bytes([0, 0, 1, 235, 128, 128, 255])) + ods + end()
        sub = parse(stream)["subtitles"][0]
        self.assertTrue(sub["forced"])
        first, second = sub["objects"]
        self.assertEqual(first["crop"], {"x": 0, "y": 0, "width": 100, "height": 2})
        self.assertIsNone(second["crop"])
        self.assertEqual((second["id"], second["x"], second["y"]), (1, 30, 40))
        self.assertEqual((second["width"], second["height"]), (100, 2))
