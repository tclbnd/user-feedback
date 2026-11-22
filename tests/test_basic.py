import unittest

from hlskit.hls import parse


class BasicTests(unittest.TestCase):
    def test_header_tags(self):
        out = parse("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:10\n#EXT-X-MEDIA-SEQUENCE:5\n#EXTINF:10,\na.ts\n#EXT-X-ENDLIST\n")
        self.assertEqual((out["type"], out["version"], out["target_duration"], out["endlist"]), ("media", 3, 10, True))

    def test_sequence(self):
        out = parse("#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:7\n#EXTINF:4,\na.ts\n#EXTINF:4,\nb.ts\n")
        self.assertEqual([s["sequence"] for s in out["segments"]], [7, 8])
