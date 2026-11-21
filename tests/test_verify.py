import struct
import unittest

from mp4kit.tags import parse
from files import (mp4, mvhd, meta, text, data, item, box, full, udta,
                   chpl, number, pair)


class VerifyTests(unittest.TestCase):
    def test_mvhd_v1(self):
        # 14-hour audiobook: 50400 s at 44100
        out = parse(mp4(mvhd(50400 * 44100, 44100, version=1)))
        self.assertEqual(out["timescale"], 44100)
        self.assertEqual(out["duration"], "50400")

    def test_utf16_text(self):
        out = parse(mp4(mvhd(1000), udta(meta(
            item("©nam", data("交響曲", 2)), item("©wrt", data("Köln", 2))))))
        self.assertEqual(out["tags"]["©nam"], "交響曲")
        self.assertEqual(out["tags"]["©wrt"], "Köln")

    def test_utf16_bom(self):
        raw = b"\xfe\xff\x00K\x00\xf6\x00l\x00n"
        out = parse(mp4(mvhd(1000), udta(meta(
            item("©nam", box("data", struct.pack(">I", 2) + b"\0" * 4 + raw))))))
        self.assertEqual(out["tags"]["©nam"], "Köln")

    def test_utf8_text(self):
        out = parse(mp4(mvhd(1000), udta(meta(text("©alb", "Zauberberg")))))
        self.assertEqual(out["tags"]["©alb"], "Zauberberg")

    def test_trkn_disk(self):
        out = parse(mp4(mvhd(1000), udta(meta(pair("trkn", 5, 9), pair("disk", 1, 0)))))
        self.assertEqual(out["tags"]["trkn"], [5, 9])
        self.assertEqual(out["tags"]["disk"], [1, None])

    def test_genre_index_is_one_based(self):
        def gnre(i):
            return item("gnre", box("data", struct.pack(">I", 0) + b"\0" * 4 + struct.pack(">H", i)))
        out = parse(mp4(mvhd(1000), udta(meta(gnre(14)))))
        self.assertEqual(out["tags"]["©gen"], "Pop")
        out = parse(mp4(mvhd(1000), udta(meta(gnre(1)))))
        self.assertEqual(out["tags"]["©gen"], "Blues")

    def test_multiple_covr(self):
        jpg = item("covr", data(b"\xff" * 10, 13), data(b"\xff" * 20, 13))
        png = item("covr", data(b"\x89PNG" * 2, 14))
        out = parse(mp4(mvhd(1000), udta(meta(jpg, png))))
        self.assertEqual(out["tags"]["covr"], [
            {"format": "jpeg", "bytes": 10},
            {"format": "jpeg", "bytes": 20},
            {"format": "png", "bytes": 8},
        ])

    def test_integer_items(self):
        out = parse(mp4(mvhd(1000), udta(meta(number("tmpo", 120, 2)))))
        self.assertEqual(out["tags"]["tmpo"], 120)

    def test_freeform(self):
        dash = box("----",
                   full("mean", 0, b"com.apple.iTunes") +
                   full("name", 0, b"iTunNORM") +
                   data("0 1 2"))
        out = parse(mp4(mvhd(1000), udta(meta(dash))))
        self.assertEqual(out["tags"]["com.apple.iTunes:iTunNORM"], "0 1 2")

    def test_chpl_scale(self):
        out = parse(mp4(mvhd(1000), udta(chpl([
            (0, "A"), (90_000_000, "B"), (132_500_000_000, "C")]))))
        self.assertEqual([c["start"] for c in out["chapters"]], ["0", "9", "13250"])
        self.assertEqual([c["title"] for c in out["chapters"]], ["A", "B", "C"])

    def test_track_level_udta_ignored(self):
        # A udta nested inside a trak must not leak into file-level tags.
        track = box("trak", box("udta", meta(text("©nam", "track-only"))))
        out = parse(mp4(mvhd(1000), track, udta(meta(text("©nam", "file-level")))))
        self.assertEqual(out["tags"], {"©nam": "file-level"})

    def test_largesize_box(self):
        # moov with a 64-bit largesize header.
        body = mvhd(1000) + udta(meta(text("©nam", "big")))
        moov = struct.pack(">I4sQ", 1, b"moov", len(body) + 16) + body
        out = parse(box("ftyp", b"M4A ") + moov)
        self.assertEqual(out["tags"]["©nam"], "big")
        self.assertEqual(out["duration"], "1")

    def test_binary_fallback_hex(self):
        out = parse(mp4(mvhd(1000), udta(meta(item("apID", data(b"\xde\xad", 0))))))
        self.assertEqual(out["tags"]["apID"], "dead")


if __name__ == "__main__":
    unittest.main()
