import unittest

from idxkit.idx import parse

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
