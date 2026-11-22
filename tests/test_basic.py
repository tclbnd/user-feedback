import json
import unittest
from pathlib import Path

from hlskit.hls import attrs, parse, urljoin

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


class BasicTests(unittest.TestCase):
    def test_header_tags(self):
        out = parse("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:10\n#EXT-X-MEDIA-SEQUENCE:5\n#EXTINF:10,\na.ts\n#EXT-X-ENDLIST\n")
        self.assertEqual((out["type"], out["version"], out["target_duration"], out["endlist"]), ("media", 3, 10, True))

    def test_sequence(self):
        out = parse("#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:7\n#EXTINF:4,\na.ts\n#EXTINF:4,\nb.ts\n")
        self.assertEqual([s["sequence"] for s in out["segments"]], [7, 8])

    def test_skip_counts_towards_sequence(self):
        out = parse("#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:100\n#EXT-X-SKIP:SKIPPED-SEGMENTS=3\n"
                    "#EXTINF:4,\nc.ts\n#EXTINF:4,\nd.ts\n")
        self.assertEqual([s["sequence"] for s in out["segments"]], [103, 104])


class DurationTests(unittest.TestCase):
    def test_extinf_is_exact_fraction(self):
        out = parse("#EXTM3U\n#EXTINF:6.006,\na.ts\n#EXTINF:3.003,\nb.ts\n")
        self.assertEqual([s["duration"] for s in out["segments"]], ["3003/500", "3003/1000"])

    def test_extinf_title_with_comma(self):
        out = parse("#EXTM3U\n#EXTINF:6.006,Cold open, part 1\na.ts\n")
        self.assertEqual(out["segments"][0]["duration"], "3003/500")

    def test_playable_duration_excludes_gaps(self):
        out = parse("#EXTM3U\n#EXTINF:6.006,\na.ts\n#EXT-X-GAP\n#EXTINF:5.0,\nb.ts\n"
                    "#EXTINF:3.003,\nc.ts\n#EXT-X-ENDLIST\n")
        self.assertEqual(out["duration"], "14009/1000")
        self.assertEqual(out["playable_duration"], "9009/1000")
        self.assertEqual(out["gaps"], 1)
        self.assertEqual([s["gap"] for s in out["segments"]], [False, True, False])

    def test_gap_only_applies_to_its_segment(self):
        out = parse("#EXTM3U\n#EXT-X-GAP\n#EXTINF:1,\na.ts\n#EXTINF:2,\nb.ts\n")
        self.assertEqual([s["gap"] for s in out["segments"]], [True, False])


class ByteRangeTests(unittest.TestCase):
    def test_explicit_offset(self):
        out = parse("#EXTM3U\n#EXTINF:1,\n#EXT-X-BYTERANGE:100@812\na.mp4\n")
        self.assertEqual(out["segments"][0]["byterange"], [812, 100])

    def test_offset_accumulates_within_same_resource(self):
        out = parse("#EXTM3U\n#EXTINF:1,\n#EXT-X-BYTERANGE:1200000@812\na.mp4\n"
                    "#EXTINF:1,\n#EXT-X-BYTERANGE:1180000\na.mp4\n"
                    "#EXTINF:1,\n#EXT-X-BYTERANGE:1210000\na.mp4\n")
        self.assertEqual([s["byterange"] for s in out["segments"]],
                         [[812, 1200000], [1200812, 1180000], [2380812, 1210000]])

    def test_offset_restarts_for_a_different_resource(self):
        out = parse("#EXTM3U\n#EXTINF:1,\n#EXT-X-BYTERANGE:100\na.mp4\n"
                    "#EXTINF:1,\n#EXT-X-BYTERANGE:100\nb.mp4\n"
                    "#EXTINF:1,\n#EXT-X-BYTERANGE:100\na.mp4\n")
        self.assertEqual([s["byterange"] for s in out["segments"]], [[0, 100], [0, 100], [0, 100]])

    def test_offset_restarts_after_a_whole_resource(self):
        out = parse("#EXTM3U\n#EXTINF:1,\n#EXT-X-BYTERANGE:100\na.mp4\n"
                    "#EXTINF:1,\na.mp4\n"
                    "#EXTINF:1,\n#EXT-X-BYTERANGE:100\na.mp4\n")
        self.assertEqual([s["byterange"] for s in out["segments"]], [[0, 100], None, [0, 100]])

    def test_map_byterange(self):
        out = parse("#EXTM3U\n#EXT-X-MAP:URI=\"init.mp4\",BYTERANGE=\"812@0\"\n#EXTINF:1,\na.mp4\n")
        self.assertEqual(out["segments"][0]["map"], {"uri": "init.mp4", "byterange": [0, 812]})


class UriTests(unittest.TestCase):
    def test_relative(self):
        self.assertEqual(urljoin("https://cdn.example/show/index.m3u8", "ep1/a.mp4"),
                         "https://cdn.example/show/ep1/a.mp4")

    def test_parent_reference(self):
        self.assertEqual(urljoin("https://cdn.example/show/index.m3u8", "../ads/a.mp4"),
                         "https://cdn.example/ads/a.mp4")

    def test_root_relative(self):
        self.assertEqual(urljoin("https://cdn.example/show/index.m3u8", "/radio/index.m3u8"),
                         "https://cdn.example/radio/index.m3u8")

    def test_base_without_trailing_slash_is_a_file(self):
        self.assertEqual(urljoin("https://cdn.example/show", "ep1/a.mp4"),
                         "https://cdn.example/ep1/a.mp4")

    def test_absolute_uri_untouched(self):
        self.assertEqual(urljoin("https://cdn.example/show/index.m3u8", "https://other.example/a.mp4"),
                         "https://other.example/a.mp4")

    def test_scheme_relative_uri(self):
        self.assertEqual(urljoin("https://cdn.example/show/index.m3u8", "//other.example/a.mp4"),
                         "https://other.example/a.mp4")

    def test_no_base(self):
        self.assertEqual(urljoin("", "../ads/a.mp4"), "../ads/a.mp4")

    def test_query_and_fragment_kept(self):
        self.assertEqual(urljoin("https://cdn.example/show/index.m3u8", "a.mp4?x=1#t=2"),
                         "https://cdn.example/show/a.mp4?x=1#t=2")

    def test_playlist_segments_resolved(self):
        out = parse("#EXTM3U\n#EXTINF:1,\n../ads/slot1.mp4\n#EXTINF:1,\n/radio/a.mp4\n",
                    "https://cdn.example/show/ep1/index.m3u8")
        self.assertEqual([s["uri"] for s in out["segments"]],
                         ["https://cdn.example/show/ads/slot1.mp4", "https://cdn.example/radio/a.mp4"])


class AttributeTests(unittest.TestCase):
    def test_quoted_values_keep_commas(self):
        a = attrs('BANDWIDTH=5400000,CODECS="avc1.640028,mp4a.40.2",RESOLUTION=1920x1080')
        self.assertEqual(a["CODECS"], "avc1.640028,mp4a.40.2")
        self.assertEqual(a["RESOLUTION"], "1920x1080")

    def test_master_codecs_full(self):
        out = parse((EXAMPLES / "master.m3u8").read_text())
        self.assertEqual([v["codecs"] for v in out["variants"]],
                         ["avc1.640028,mp4a.40.2", "avc1.4d401f,mp4a.40.2", "mp4a.40.2"])
        self.assertEqual(out["renditions"], 2)

    def test_master_uri_resolution(self):
        out = parse((EXAMPLES / "master.m3u8").read_text(), "https://cdn.example/show/index.m3u8")
        self.assertEqual([v["uri"] for v in out["variants"]],
                         ["https://cdn.example/show/video/1080p/index.m3u8",
                          "https://cdn.example/show/video/720p/index.m3u8",
                          "https://cdn.example/radio/index.m3u8"])


class KeyTests(unittest.TestCase):
    def test_iv_from_attribute(self):
        out = parse('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k",IV=0x0000000000000000000000000000002A\n#EXTINF:1,\na.ts\n')
        self.assertEqual(out["segments"][0]["key"], "AES-128")
        self.assertEqual(out["segments"][0]["iv"], "0x0000000000000000000000000000002a")

    def test_iv_derived_from_sequence(self):
        out = parse('#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:5\n#EXT-X-KEY:METHOD=AES-128,URI="k"\n#EXTINF:1,\na.ts\n')
        self.assertEqual(out["segments"][0]["iv"], "0x%032x" % 5)

    def test_method_none_clears_key(self):
        out = parse('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k"\n#EXTINF:1,\na.ts\n'
                    '#EXT-X-KEY:METHOD=NONE\n#EXTINF:1,\nb.ts\n')
        self.assertEqual([s["key"] for s in out["segments"]], ["AES-128", None])
        self.assertEqual([s["iv"] for s in out["segments"]], ["0x%032x" % 0, None])


class ExampleTests(unittest.TestCase):
    def test_episode(self):
        out = parse((EXAMPLES / "episode.m3u8").read_text(), "https://cdn.example/show/episode.m3u8")
        self.assertEqual(out["type"], "media")
        self.assertEqual([s["duration"] for s in out["segments"]],
                         ["3003/500"] * 3 + ["5", "3003/500", "3003/1000"])
        self.assertEqual([s["byterange"] for s in out["segments"]],
                         [[812, 1200000], [1200812, 1180000], [2380812, 1210000], None,
                          [3590812, 1190000], [4780812, 600000]])
        self.assertEqual([s["uri"] for s in out["segments"]],
                         ["https://cdn.example/show/ep1/main.mp4"] * 3 +
                         ["https://cdn.example/ads/slot1.mp4"] +
                         ["https://cdn.example/show/ep1/main.mp4"] * 2)
        self.assertEqual([s["gap"] for s in out["segments"]], [False, False, False, True, False, False])
        self.assertEqual([s["discontinuity"] for s in out["segments"]], [False, False, False, True, True, False])
        self.assertEqual(out["segments"][0]["program_date_time"], "2026-11-20T19:30:00.000+08:00")
        self.assertEqual(out["segments"][3]["map"], {"uri": "https://cdn.example/ads/init.mp4", "byterange": None})
        self.assertEqual(out["duration"], "32027/1000")
        self.assertEqual(out["playable_duration"], "27027/1000")
        self.assertEqual((out["gaps"], out["discontinuities"]), (1, 2))
        json.dumps(out)  # must stay JSON-serialisable
