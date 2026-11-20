import unittest

from vttkit import qc, srt
from vttkit.parser import VttError, cue_text, parse, settings, timestamp


class BasicTests(unittest.TestCase):
    def test_simple(self):
        cues = parse("WEBVTT\n\n00:00:01.000 --> 00:00:03.500\nHello there\n")
        self.assertEqual((cues[0]["start"], cues[0]["end"], cues[0]["text"]), (1000, 3500, "Hello there"))
        self.assertEqual(srt.export(cues), "1\n00:00:01,000 --> 00:00:03,500\nHello there\n")

    def test_qc(self):
        cues = [{"start": 0, "end": 1000, "text": "x" * 30}]
        self.assertEqual(qc.report(cues), [{"cue": 0, "issue": "too fast"}])


class HeaderTests(unittest.TestCase):
    def test_bom_and_header_text(self):
        cues = parse("﻿WEBVTT - Drama title\n\n00:01.000 --> 00:02.000\nx\n")
        self.assertEqual(len(cues), 1)

    def test_crlf_and_cr_normalized(self):
        cues = parse("WEBVTT\r\n\r\n00:01.000 --> 00:02.000\r\nline one\r\nline two\r\n")
        self.assertEqual(cues[0]["text"], "line one\nline two")
        cues = parse("WEBVTT\r\r00:01.000 --> 00:02.000\rx\r")
        self.assertEqual(cues[0]["text"], "x")

    def test_header_metadata_lines_tolerated(self):
        text = "WEBVTT - Show\nKind: captions\nLanguage: en\n\n00:01.000 --> 00:02.000\nx\n"
        self.assertEqual(parse(text)[0]["text"], "x")

    def test_missing_header_raises(self):
        with self.assertRaises(VttError):
            parse("00:01.000 --> 00:02.000\nx\n")
        with self.assertRaises(VttError):
            parse("WEBVTTX\n")
        with self.assertRaises(VttError):
            parse("")


class TimestampTests(unittest.TestCase):
    def test_short_form_without_hours(self):
        self.assertEqual(timestamp("01:02.500"), 62500)
        self.assertEqual(timestamp("00:00.800"), 800)

    def test_full_form(self):
        self.assertEqual(timestamp("01:00:00.000"), 3600000)
        self.assertEqual(timestamp("100:00:00.000"), 360000000)

    def test_bad_timestamps(self):
        for bad in ("1:02.500", "01:02.5", "01:02,500", "00:99.000", "60:00.000", "nope"):
            with self.assertRaises(VttError):
                timestamp(bad)


class BlockTests(unittest.TestCase):
    def test_note_style_region_blocks_ignored(self):
        text = (
            "WEBVTT\n\n"
            "NOTE\nmulti\nline note\n\n"
            "NOTE inline comment\n\n"
            "STYLE\n::cue { color: red; }\n\n"
            "REGION\nid:top\nwidth:50%\n\n"
            "00:01.000 --> 00:02.000\nkept\n"
        )
        cues = parse(text)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0]["text"], "kept")

    def test_note_without_space_prefix_is_an_identifier(self):
        # "NOTEPAD" is not a NOTE block keyword; per spec it is a cue id.
        cues = parse("WEBVTT\n\nNOTEPAD\n00:01.000 --> 00:02.000\nx\n")
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0]["id"], "NOTEPAD")

    def test_identifier_without_timing_line_dropped(self):
        cues = parse("WEBVTT\n\norphan-id\n\n00:03.000 --> 00:04.000\ny\n")
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0]["text"], "y")


class CueTests(unittest.TestCase):
    def test_cue_identifier_kept(self):
        cues = parse("WEBVTT\n\ncue-7\n00:01.000 --> 00:02.000\nx\n")
        self.assertEqual(cues[0]["id"], "cue-7")
        self.assertIsNone(parse("WEBVTT\n\n00:01.000 --> 00:02.000\nx\n")[0]["id"])

    def test_bad_cue_dropped_but_file_survives(self):
        text = (
            "WEBVTT\n\n"
            "00:01.000 --> 00:02.000\ngood one\n\n"
            "00:99.000 --> 00:03.000\nbad minutes\n\n"
            "00:04.000 --> 00:05.000\nalso good\n"
        )
        cues = parse(text)
        self.assertEqual([c["text"] for c in cues], ["good one", "also good"])

    def test_comma_decimal_timestamp_rejected_per_cue(self):
        cues = parse("WEBVTT\n\n00:01,000 --> 00:02,000\nx\n")
        self.assertEqual(cues, [])

    def test_empty_payload_kept(self):
        cues = parse("WEBVTT\n\n00:01.000 --> 00:02.000\n\n00:03.000 --> 00:04.000\ny\n")
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["text"], "")


class SettingsTests(unittest.TestCase):
    def test_valid_settings_kept_verbatim(self):
        self.assertEqual(
            settings("align:start position:10%,line-left"),
            {"align": "start", "position": "10%,line-left"},
        )
        self.assertEqual(settings("line:85% align:center"), {"line": "85%", "align": "center"})
        self.assertEqual(settings("vertical:rl line:-3 size:40% region:top"),
                         {"vertical": "rl", "line": "-3", "size": "40%", "region": "top"})

    def test_out_of_range_and_unknown_settings_dropped(self):
        self.assertEqual(settings("size:200% align:middle line:50%"), {"line": "50%"})
        self.assertEqual(settings("position:150% vertical:tb"), {})
        self.assertEqual(settings("bogus:value align:end"), {"align": "end"})
        self.assertEqual(settings("size:101% line:120%"), {})

    def test_repeated_setting_keeps_first(self):
        self.assertEqual(settings("align:end align:start"), {"align": "end"})

    def test_malformed_setting_dropped(self):
        self.assertEqual(settings("align no-value: size:50%"), {"size": "50%"})


class PayloadTests(unittest.TestCase):
    def test_voice_extracted(self):
        cues = parse("WEBVTT\n\n00:01.000 --> 00:02.000\n<v Mira>hello</v>\n")
        self.assertEqual(cues[0]["voice"], "Mira")
        cues = parse("WEBVTT\n\n00:01.000 --> 00:02.000\n<v.loud Tomas>yo</v>\n")
        self.assertEqual(cues[0]["voice"], "Tomas")
        self.assertIsNone(parse("WEBVTT\n\n00:01.000 --> 00:02.000\nplain\n")[0]["voice"])

    def test_entities_decoded(self):
        self.assertEqual(cue_text("a &amp; b &lt; c &gt; d&nbsp;e"), "a & b < c > d e")
        self.assertEqual(cue_text("num &#65; hex &#x42;"), "num A hex B")
        # Only the four spec named entities exist; others stay literal.
        self.assertEqual(cue_text("&mdash;"), "&mdash;")
        # Forbidden numeric references become U+FFFD.
        self.assertEqual(cue_text("&#0;"), "�")

    def test_tags_stripped(self):
        self.assertEqual(cue_text("<i>Not</i> until <b>now</b>"), "Not until now")
        self.assertEqual(cue_text("time <00:01:02.000>stamp"), "time stamp")
        self.assertEqual(cue_text("<c.sfx>&lt;bell&gt;</c>"), "<bell>")

    def test_multiline_payload_preserved(self):
        cues = parse("WEBVTT\n\n00:01.000 --> 00:02.000\nline one\nline two\n")
        self.assertEqual(cues[0]["text"], "line one\nline two")


class SrtExportTests(unittest.TestCase):
    def test_export_follows_parse_result(self):
        text = (
            "WEBVTT\n\n"
            "NOTE\nskip me\n\n"
            "1\n00:01.000 --> 00:02.500 size:200%\n<v A>hi &amp; bye</v>\n\n"
            "2\n00:03.000 --> 00:04.000\nsecond\n"
        )
        out = srt.export(parse(text))
        self.assertEqual(
            out,
            "1\n00:00:01,000 --> 00:00:02,500\nhi & bye\n"
            "\n2\n00:00:03,000 --> 00:00:04,000\nsecond\n",
        )


if __name__ == "__main__":
    unittest.main()
