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
