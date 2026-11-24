import unittest

from idxkit.idx import IdxError, parse

HEAD = """# VobSub index file, v7 (do not modify this line!)
size: 720x576
palette: 000000, f0f0f0, cccccc, 999999, 3333fa, 1111bb, fa3333, bb1111, 33fa33, 11bb11, fafa33, bbbb11, fa33fa, bb11bb, 33fafa, 11bbbb
"""


def build(body, head=HEAD):
    return head + body


class TimestampTests(unittest.TestCase):
    def test_last_field_is_milliseconds(self):
        out = parse(build("id: en, index: 0\ntimestamp: 00:00:01:234, filepos: 000000000\n"))
        self.assertEqual((out["languages"][0]["first"], out["languages"][0]["last"]), ("617/500", "617/500"))

    def test_time_offset_applies(self):
        out = parse(build("time offset: -1000\nid: en, index: 0\ntimestamp: 00:00:01:500, filepos: 000000000\n"))
        self.assertEqual(out["languages"][0]["first"], "1/2")

    def test_delays_accumulate(self):
        body = ("id: en, index: 0\n"
                "timestamp: 00:00:10:000, filepos: 000000000\n"
                "delay: 00:00:01:200\n"
                "timestamp: 00:00:20:000, filepos: 000000000\n"
                "delay: -00:00:00:300\n"
                "timestamp: 00:00:30:000, filepos: 000000000\n")
        lang = parse(build(body))["languages"][0]
        self.assertEqual((lang["delays"], lang["first"], lang["last"]), (2, "10", "309/10"))

    def test_negative_delay_line_parses(self):
        body = ("id: en, index: 0\n"
                "delay: -00:00:00:500\n"
                "timestamp: 00:00:02:000, filepos: 000000000\n")
        lang = parse(build(body))["languages"][0]
        self.assertEqual((lang["subtitles"], lang["first"]), (1, "3/2"))

    def test_backwards_uses_shifted_times(self):
        body = ("id: en, index: 0\n"
                "timestamp: 00:00:02:000, filepos: 000000000\n"
                "timestamp: 00:00:01:800, filepos: 000000000\n")
        self.assertEqual(parse(build(body))["languages"][0]["backwards"], 1)


class LanguageTests(unittest.TestCase):
    def test_index_comes_from_id_line(self):
        body = ("id: en, index: 0\ntimestamp: 00:00:01:000, filepos: 000000000\n"
                "id: de, index: 4\ntimestamp: 00:00:01:000, filepos: 000000000\n"
                "id: es, index: 7\ntimestamp: 00:00:01:000, filepos: 000000000\n")
        self.assertEqual([l["index"] for l in parse(build(body))["languages"]], [0, 4, 7])

    def test_delays_do_not_leak_between_languages(self):
        body = ("id: en, index: 0\n"
                "delay: 00:00:01:000\n"
                "timestamp: 00:00:01:000, filepos: 000000000\n"
                "id: de, index: 1\n"
                "timestamp: 00:00:01:000, filepos: 000000000\n")
        langs = parse(build(body))["languages"]
        self.assertEqual([l["first"] for l in langs], ["2", "1"])


class CustomColorTests(unittest.TestCase):
    def test_off_means_no_custom_colors(self):
        out = parse(build("custom colors: OFF, tridx: 1000, colors: ffffff, 000000, ffff00, 808080\n"))
        self.assertIsNone(out["custom_colors"])

    def test_on_lists_four_colors(self):
        out = parse(build("custom colors: ON, tridx: 1000, colors: ffffff, 000000, ffff00, 808080\n"))
        self.assertEqual(out["custom_colors"], ["#ffffff", "#000000", "#ffff00", "#808080"])


class HeaderTests(unittest.TestCase):
    def test_no_version(self):
        self.assertIsNone(parse("# VobSub index file\nsize: 720x480\n")["version"])

    def test_not_index(self):
        with self.assertRaises(IdxError):
            parse("not an index\n")

    def test_other_tool_output(self):
        # BOM, CRLF, upper-case keywords, 0x-prefixed colours, unpadded milliseconds.
        text = (chr(0xFEFF) + "# VobSub index file, v7\r\n"
                "SIZE: 720x480\r\n"
                "PALETTE: 0x000000, 0xFFFFFF\r\n"
                "ID: EN, INDEX: 3\r\n"
                "TIMESTAMP: 00:00:01:5, FILEPOS: 000000000\r\n")
        out = parse(text)
        self.assertEqual((out["version"], out["size"], out["palette"]),
                         (7, [720, 480], ["#000000", "#ffffff"]))
        self.assertEqual((out["languages"][0]["id"], out["languages"][0]["index"],
                          out["languages"][0]["first"]), ("EN", 3, "201/200"))
