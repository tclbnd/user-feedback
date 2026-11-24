import unittest

from lrckit.lrc import parse

TEXT = "[ti:Song]\n[ar:Band]\n[00:01.000]First\n[00:02.500]Second\n"


class BasicTests(unittest.TestCase):
    def test_tags(self):
        out = parse(TEXT)
        self.assertEqual((out["title"], out["artist"], out["offset_ms"]), ("Song", "Band", 0))

    def test_lines(self):
        self.assertEqual([l["text"] for l in parse(TEXT)["lines"]], ["First", "Second"])
