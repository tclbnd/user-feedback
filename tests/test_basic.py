import unittest

from idxkit.idx import IdxError, parse

SAMPLE = """# VobSub index file, v7 (do not modify this line!)
size: 720x480
palette: 000000, ffffff, 808080, 404040, 000000, ffffff, 808080, 404040, 000000, ffffff, 808080, 404040, 000000, ffffff, 808080, 404040
langidx: 0

id: en, index: 0
timestamp: 00:00:05:000, filepos: 000000000
timestamp: 00:01:00:000, filepos: 000000800
"""


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(SAMPLE)
        self.assertEqual((out["version"], out["size"], out["palette"][1]), (7, [720, 480], "#ffffff"))

    def test_language(self):
        lang = parse(SAMPLE)["languages"][0]
        self.assertEqual((lang["id"], lang["subtitles"], lang["first"], lang["last"]), ("en", 2, "5", "60"))


HEADER = "# VobSub index file, v7 (do not modify this line!)\n"


class TimestampTests(unittest.TestCase):
    def test_milliseconds_are_not_scaled(self):
        # The last field is milliseconds already: 500 ms = 1/2 s, not 5 s.
        text = HEADER + "id: en, index: 0\ntimestamp: 00:00:01:500, filepos: 0\n"
        lang = parse(text)["languages"][0]
        self.assertEqual((lang["first"], lang["last"]), ("3/2", "3/2"))

    def test_fractional_seconds(self):
        text = HEADER + "id: en, index: 0\ntimestamp: 00:00:00:001, filepos: 0\n"
        self.assertEqual(parse(text)["languages"][0]["first"], "1/1000")

    def test_negative_timestamp(self):
        text = HEADER + "id: en, index: 0\ntimestamp: -00:00:01:500, filepos: 0\n"
        self.assertEqual(parse(text)["languages"][0]["first"], "-3/2")

    def test_backwards_detection(self):
        text = (HEADER + "id: en, index: 0\n"
                "timestamp: 00:00:02:000, filepos: 0\n"
                "timestamp: 00:00:01:000, filepos: 1\n")
        self.assertEqual(parse(text)["languages"][0]["backwards"], 1)


class DelayTests(unittest.TestCase):
    def test_positive_delay_shifts_following_timestamps(self):
        text = (HEADER + "id: en, index: 0\n"
                "timestamp: 00:00:01:000, filepos: 0\n"
                "delay: 00:00:01:200\n"
                "timestamp: 00:00:02:000, filepos: 1\n")
        lang = parse(text)["languages"][0]
        self.assertEqual(lang["delays"], 1)
        self.assertEqual((lang["first"], lang["last"]), ("1", "16/5"))  # 2.000 + 1.200

    def test_delays_accumulate(self):
        text = (HEADER + "id: en, index: 0\n"
                "delay: 00:00:01:000\n"
                "delay: -00:00:00:400\n"
                "timestamp: 00:00:10:000, filepos: 0\n")
        lang = parse(text)["languages"][0]
        self.assertEqual(lang["delays"], 2)
        self.assertEqual(lang["first"], "53/5")  # 10.000 + 1.000 - 0.400

    def test_negative_delay_does_not_reject_file(self):
        text = (HEADER + "id: en, index: 0\n"
                "delay: -00:00:00:500\n"
                "timestamp: 00:00:02:000, filepos: 0\n")
        lang = parse(text)["languages"][0]
        self.assertEqual((lang["delays"], lang["first"]), (1, "3/2"))

    def test_delay_is_per_language(self):
        text = (HEADER
                + "id: en, index: 0\ndelay: 00:00:05:000\ntimestamp: 00:00:01:000, filepos: 0\n"
                + "id: de, index: 4\ntimestamp: 00:00:01:000, filepos: 0\n")
        langs = parse(text)["languages"]
        self.assertEqual(langs[0]["first"], "6")
        self.assertEqual(langs[1]["first"], "1")

    def test_bad_delay_line_raises(self):
        text = HEADER + "id: en, index: 0\ndelay: garbage\n"
        with self.assertRaises(IdxError):
            parse(text)


class OffsetTests(unittest.TestCase):
    def test_time_offset_applies_to_every_timestamp(self):
        text = (HEADER + "time offset: 250\nid: en, index: 0\n"
                "timestamp: 00:00:01:000, filepos: 0\n"
                "timestamp: 00:00:02:000, filepos: 1\n")
        lang = parse(text)["languages"][0]
        self.assertEqual((lang["first"], lang["last"]), ("5/4", "9/4"))

    def test_negative_time_offset_with_delay(self):
        text = (HEADER + "time offset: -1000\nid: en, index: 0\n"
                "delay: -00:00:00:500\n"
                "timestamp: 00:00:02:000, filepos: 0\n")
        self.assertEqual(parse(text)["languages"][0]["first"], "1/2")  # 2 - 0.5 - 1


class LanguageTests(unittest.TestCase):
    def test_stream_index_comes_from_id_line(self):
        text = (HEADER
                + "id: en, index: 0\ntimestamp: 00:00:01:000, filepos: 0\n"
                + "id: de, index: 4\ntimestamp: 00:00:01:000, filepos: 0\n")
        langs = parse(text)["languages"]
        self.assertEqual([lang["index"] for lang in langs], [0, 4])

    def test_missing_index_falls_back_to_ordinal(self):
        text = HEADER + "id: en\ntimestamp: 00:00:01:000, filepos: 0\n"
        self.assertEqual(parse(text)["languages"][0]["index"], 0)


class CustomColorsTests(unittest.TestCase):
    def test_off_leaves_null(self):
        text = HEADER + "custom colors: OFF, tridx: 0000, colors: 000000, 000000, 000000, 000000\n"
        self.assertIsNone(parse(text)["custom_colors"])

    def test_on_returns_four_colors(self):
        text = HEADER + "custom colors: ON, tridx: 1000, colors: ffffff, 000000, ffff00, 808080\n"
        self.assertEqual(parse(text)["custom_colors"],
                         ["#ffffff", "#000000", "#ffff00", "#808080"])

    def test_on_without_color_list(self):
        text = HEADER + "custom colors: ON\n"
        self.assertEqual(parse(text)["custom_colors"], [])

    def test_default_is_null(self):
        self.assertIsNone(parse(HEADER)["custom_colors"])


class ToolFlavourTests(unittest.TestCase):
    def test_crlf_and_utf8_bom(self):
        text = "﻿# VobSub index file, v7\r\nid: en, index: 0\r\ntimestamp: 00:00:01:500, filepos: 0\r\n"
        self.assertEqual(parse(text)["languages"][0]["first"], "3/2")

    def test_uppercase_keywords_and_0x_filepos(self):
        text = HEADER + "id: en, index: 0\nTIMESTAMP: 00:00:01:500, FILEPOS: 0x800\n"
        self.assertEqual(parse(text)["languages"][0]["subtitles"], 1)

    def test_comments_and_blank_lines_ignored(self):
        text = (HEADER + "# a comment\n\nid: en, index: 0  # inline style note\n"
                "timestamp: 00:00:01:000, filepos: 0\n")
        self.assertEqual(parse(text)["languages"][0]["subtitles"], 1)

    def test_missing_version(self):
        text = "# VobSub index file\nid: en, index: 0\ntimestamp: 00:00:01:000, filepos: 0\n"
        out = parse(text)
        self.assertIsNone(out["version"])
        self.assertEqual(out["languages"][0]["first"], "1")

    def test_not_an_idx_file(self):
        with self.assertRaises(IdxError):
            parse("completely unrelated text\n")


if __name__ == "__main__":
    unittest.main()
