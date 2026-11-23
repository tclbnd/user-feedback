"""Regression tests for real-world PGS quirks: timing, multi-fragment objects,
crops, empty display sets and palette-only display sets."""
import struct
import unittest

from supkit.sup import parse, seconds
from fractions import Fraction

PDS, ODS, PCS, WDS, END = 0x14, 0x15, 0x16, 0x17, 0x80


def seg(kind, body, pts=0):
    return b"PG" + struct.pack(">IIBH", pts, 0, kind, len(body)) + body


def pcs(pts, comps=(), state=0x80, pal_update=0, pal_id=0):
    body = struct.pack(">HHBHBBBB", 1920, 1080, 0x10, 0, state, pal_update,
                       pal_id, len(comps))
    for oid, wid, flags, x, y, crop in comps:
        body += struct.pack(">HBBHH", oid, wid, flags, x, y)
        if crop is not None:
            body += struct.pack(">HHHH", *crop)
    return seg(PCS, body, pts)


def wds(pts, windows=((0, 0, 800, 1920, 200),), state_pts=None):
    body = bytes([len(windows)])
    for wid, x, y, w, h in windows:
        body += struct.pack(">BHHHH", wid, x, y, w, h)
    return seg(WDS, body, pts)


def pds(pts, entries=((1, 235, 128, 128, 255),), pal_id=0, version=0):
    body = bytes([pal_id, version])
    for entry, y, cb, cr, alpha in entries:
        body += bytes([entry, y, cb, cr, alpha])
    return seg(PDS, body, pts)


def rle_bitmap(width, height, opaque_cols, colour=1):
    """One run of ``colour`` for ``opaque_cols`` pixels, then colour 0, per row."""
    def run(count, run_colour):
        # 14-bit run length: 0x00, then two length bytes with type in the
        # top bits of the first, then the colour byte.
        return bytes([0, 0xC0 | ((count >> 8) & 0x3F), count & 0xFF, run_colour])

    row = run(opaque_cols, colour) + run(width - opaque_cols, 0) + b"\x00\x00"
    return row * height


def ods_single(oid, width, height, picture, pts=0):
    obj = struct.pack(">HH", width, height) + picture
    body = struct.pack(">HBB", oid, 0, 0xC0) + (len(obj) + 4).to_bytes(3, "big") + obj
    return seg(ODS, body, pts)


def ods_split(oid, width, height, picture, cut, pts=0):
    """Object split into a first (0x80) and a continuation (0x40) fragment."""
    first, second = picture[:cut], picture[cut:]
    total = 4 + len(picture) + 4
    b1 = struct.pack(">HBB", oid, 0, 0x80) + total.to_bytes(3, "big") \
        + struct.pack(">HH", width, height) + first
    b2 = struct.pack(">HBB", oid, 0, 0x40) + (len(second) + 4).to_bytes(3, "big") + second
    return seg(ODS, b1, pts) + seg(ODS, b2, pts)


def end(pts):
    return seg(END, b"", pts)


def epoch_start(pts, comps, picture_for, windows=None, palette_entries=None):
    out = pcs(pts, comps)
    out += wds(pts) if windows is None else wds(pts, windows)
    out += pds(pts) if palette_entries is None else pds(pts, palette_entries)
    for oid, width, height, picture in picture_for:
        out += ods_single(oid, width, height, picture, pts)
    out += end(pts)
    return out


def clear(pts):
    return pcs(pts, (), state=0x00) + wds(pts) + end(pts)


class TimingTests(unittest.TestCase):
    def test_pts_uses_90khz_clock(self):
        self.assertEqual(seconds(90000), "1")
        self.assertEqual(seconds(45000), "1/2")
        self.assertEqual(Fraction(seconds(5985000)), Fraction(133, 2))

    def test_start_and_end_seconds(self):
        pic = rle_bitmap(10, 2, 6)
        stream = epoch_start(5580000, [(0, 0, 0, 100, 200, None)], [(0, 10, 2, pic)])
        stream += clear(5850000)
        subs = parse(stream)["subtitles"]
        self.assertEqual(len(subs), 1)
        self.assertEqual(Fraction(subs[0]["start"]), 62)
        self.assertEqual(Fraction(subs[0]["end"]), 65)

    def test_clear_display_set_is_not_a_subtitle(self):
        pic = rle_bitmap(10, 2, 6)
        stream = epoch_start(0, [(0, 0, 0, 0, 0, None)], [(0, 10, 2, pic)])
        stream += clear(90000)
        out = parse(stream)
        self.assertEqual(out["display_sets"], 2)
        self.assertEqual(len(out["subtitles"]), 1)
        self.assertEqual(Fraction(out["subtitles"][0]["end"]), 1)


class ObjectTests(unittest.TestCase):
    def test_split_object_is_reassembled(self):
        pic = rle_bitmap(1500, 70, 1400)
        stream = pcs(0, [(0, 0, 0, 10, 20, None)]) + wds(0) + pds(0)
        stream += ods_split(0, 1500, 70, pic, 348)
        stream += end(0)
        obj = parse(stream)["subtitles"][0]["objects"][0]
        self.assertEqual((obj["width"], obj["height"]), (1500, 70))
        self.assertEqual(obj["opaque_pixels"], 1400 * 70)

    def test_cropped_object_followed_by_second_object(self):
        pic0 = rle_bitmap(500, 50, 400)
        pic1 = rle_bitmap(700, 50, 600)
        comps = [(0, 0, 0x80, 400, 850, (0, 0, 500, 50)),
                 (1, 0, 0x00, 400, 940, None)]
        stream = pcs(0, comps) + wds(0, ((0, 400, 850, 1100, 160),)) + pds(0)
        stream += ods_single(0, 500, 50, pic0) + ods_single(1, 700, 50, pic1)
        stream += end(0)
        sub = parse(stream)["subtitles"][0]
        o0, o1 = sub["objects"]
        self.assertEqual(o0["crop"], {"x": 0, "y": 0, "width": 500, "height": 50})
        self.assertEqual((o0["x"], o0["y"], o0["width"], o0["height"]),
                         (400, 850, 500, 50))
        self.assertIsNone(o1["crop"])
        self.assertEqual((o1["id"], o1["x"], o1["y"], o1["width"], o1["height"]),
                         (1, 400, 940, 700, 50))
        self.assertEqual(o1["opaque_pixels"], 600 * 50)
        self.assertEqual(sub["windows"],
                         [{"id": 0, "x": 400, "y": 850, "width": 1100, "height": 160}])

    def test_undefined_palette_entry_is_transparent(self):
        # All pixels reference palette entry 3, which the PDS never defines.
        pic = rle_bitmap(10, 4, 10, colour=3)
        stream = epoch_start(0, [(0, 0, 0, 0, 0, None)], [(0, 10, 4, pic)])
        obj = parse(stream)["subtitles"][0]["objects"][0]
        self.assertEqual(obj["opaque_pixels"], 0)


class PaletteTests(unittest.TestCase):
    def test_palette_only_display_set_continues_subtitle(self):
        pic = rle_bitmap(20, 4, 10)
        stream = epoch_start(0, [(0, 0, 0, 5, 5, None)], [(0, 20, 4, pic)])
        stream += pcs(9000, [(0, 0, 0, 5, 5, None)], state=0x00, pal_update=0x80)
        stream += pds(9000, ((1, 235, 128, 128, 128),), version=1) + end(9000)
        stream += pcs(18000, [(0, 0, 0, 5, 5, None)], state=0x00, pal_update=0x80)
        stream += pds(18000, ((1, 235, 128, 128, 255),), version=1) + end(18000)
        stream += clear(27000)
        out = parse(stream)
        self.assertEqual(out["display_sets"], 4)
        self.assertEqual(len(out["subtitles"]), 1)
        sub = out["subtitles"][0]
        self.assertEqual([Fraction(v) for v in sub["palette_updates"]],
                         [Fraction(1, 10), Fraction(1, 5)])
        self.assertEqual(Fraction(sub["end"]), Fraction(3, 10))

    def test_palette_state_uses_alpha_of_each_entry(self):
        pic = rle_bitmap(10, 2, 5)
        # Entry 1 half transparent: pixels still count as non-transparent.
        stream = epoch_start(0, [(0, 0, 0, 0, 0, None)], [(0, 10, 2, pic)],
                             palette_entries=((1, 235, 128, 128, 40),))
        obj = parse(stream)["subtitles"][0]["objects"][0]
        self.assertEqual(obj["opaque_pixels"], 10)
        self.assertEqual(parse(stream)["palettes"],
                         {"0": {"1": [235, 128, 128, 40]}})

    def test_palette_entries_accumulate_across_pds(self):
        pic = rle_bitmap(10, 1, 4)
        stream = pcs(0, [(0, 0, 0, 0, 0, None)]) + wds(0)
        stream += pds(0, ((1, 235, 128, 128, 255),))
        stream += ods_single(0, 10, 1, pic) + end(0)
        stream += pcs(9000, [(0, 0, 0, 0, 0, None)], state=0x00, pal_update=0x80)
        stream += pds(9000, ((2, 100, 128, 128, 255),), version=1) + end(9000)
        palettes = parse(stream)["palettes"]
        self.assertEqual(palettes["0"]["1"], [235, 128, 128, 255])
        self.assertEqual(palettes["0"]["2"], [100, 128, 128, 255])


class EpochTests(unittest.TestCase):
    def test_new_epoch_resets_objects_and_palette(self):
        pic = rle_bitmap(8, 2, 4)
        stream = epoch_start(0, [(5, 0, 0, 0, 0, None)], [(5, 8, 2, pic)])
        stream += clear(90000)
        # New epoch reuses object id 5 with a fresh bitmap and palette.
        pic2 = rle_bitmap(6, 2, 3)
        stream += epoch_start(180000, [(5, 0, 0, 0, 0, None)],
                              [(5, 6, 2, pic2)],
                              palette_entries=((1, 200, 128, 128, 255),))
        out = parse(stream)
        self.assertEqual(out["epochs"], 2)
        self.assertEqual(len(out["subtitles"]), 2)
        last = out["subtitles"][1]["objects"][0]
        self.assertEqual((last["width"], last["height"], last["opaque_pixels"]),
                         (6, 2, 6))
        self.assertEqual(set(out["palettes"]["0"].keys()), {"1"})
        self.assertEqual(out["palettes"]["0"]["1"], [200, 128, 128, 255])

    def test_forced_flag_is_collected_from_composition_object(self):
        pic = rle_bitmap(8, 2, 4)
        stream = epoch_start(0, [(0, 0, 0x40, 0, 0, None)], [(0, 8, 2, pic)])
        self.assertTrue(parse(stream)["subtitles"][0]["forced"])


if __name__ == "__main__":
    unittest.main()
