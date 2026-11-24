import unittest

from ttmlkit.ttml import parse

from docs import doc


class BasicTests(unittest.TestCase):
    def test_clock_times(self):
        out = parse(doc([("00:00:01.500", "00:00:03", "Hello")]))
        self.assertEqual((out["paragraphs"][0]["begin"], out["paragraphs"][0]["end"]), ("3/2", "3"))

    def test_seconds_offset(self):
        self.assertEqual(parse(doc([("2s", "4.25s", "Hi")]))["duration"], "17/4")
