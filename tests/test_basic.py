import unittest

from asskit.ass import parse

SCRIPT = """[Script Info]
ScriptType: v4.00+
PlayResX: 640
PlayResY: 360

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Hello
"""


class BasicTests(unittest.TestCase):
    def test_info(self):
        out = parse(SCRIPT)
        self.assertEqual((out["script_type"], out["play_res"]), ("v4.00+", [640, 360]))

    def test_event_text(self):
        self.assertEqual(parse(SCRIPT)["events"][0]["lines"], ["Hello"])


class SpecificationTests(unittest.TestCase):
    def test_commas_in_text_are_kept(self):
        out = parse("""[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Hello, world, again
""")
        self.assertEqual(out["events"][0]["lines"], ["Hello, world, again"])

    def test_time_fraction_is_hundredths(self):
        out = parse(SCRIPT.replace("0:00:01.00", "0:00:12.10").replace("0:00:02.00", "1:02:03.45"))
        self.assertEqual((out["events"][0]["start"], out["events"][0]["end"]), ("121/10", "74469/20"))

    def test_colour_is_bbggrr(self):
        out = parse(SCRIPT.replace("v4.00+", "v4.00").replace("[Events]", """[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, BackColour, Bold, Italic, Alignment
Style: Default,Arial,48,&H00FF0000,&H00000000,0,0,2

[Events]"""))
        self.assertEqual(out["styles"]["Default"]["color"], "#0000ff")

    def test_reordered_style_format(self):
        out = parse("""[V4+ Styles]
Format: Alignment, Name, Fontname, Fontsize, PrimaryColour
Style: 8,Sign,Arial,30,&H00FFFFFF

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""")
        self.assertEqual(out["styles"]["Sign"], {"font": "Arial", "size": 30.0,
                                                 "color": "#ffffff", "opacity": 255, "alignment": 8})

    def test_ssa_alignment_to_numpad(self):
        out = parse("""[Script Info]
ScriptType: v4.00

[V4 Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, Alignment
Style: Top,Tahoma,30,&H00FFFFFF,6
Style: Mid,Tahoma,30,&H00FFFFFF,10

[Events]
Format: Marked, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: Marked=0,0:00:01.00,0:00:02.00,Top,,0,0,0,,{\\a6}line
""")
        self.assertEqual(out["styles"]["Top"]["alignment"], 8)
        self.assertEqual(out["styles"]["Mid"]["alignment"], 5)
        self.assertEqual(out["events"][0]["alignment"], 8)
        self.assertEqual(out["events"][0]["layer"], 0)
