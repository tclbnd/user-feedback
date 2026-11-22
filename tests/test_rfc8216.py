import unittest

from hlskit.hls import HlsError, parse

BASE = "https://cdn.example/show/index.m3u8"


class DurationTests(unittest.TestCase):
    def test_decimal_duration_is_exact_fraction(self):
        out = parse("#EXTM3U\n#EXT-X-TARGETDURATION:7\n"
                    "#EXTINF:6.006,\na.ts\n#EXTINF:3.003,\nb.ts\n#EXTINF:5.0,\nc.ts\n")
        self.assertEqual([s["duration"] for s in out["segments"]],
                         ["3003/500", "3003/1000", "5"])
        self.assertEqual(out["duration"], "14009/1000")

    def test_integer_duration(self):
        out = parse("#EXTM3U\n#EXTINF:10,\na.ts\n")
        self.assertEqual(out["segments"][0]["duration"], "10")


class ByterangeTests(unittest.TestCase):
    PLAYLIST = (
        "#EXTM3U\n#EXT-X-VERSION:7\n"
        "#EXTINF:6,\n#EXT-X-BYTERANGE:1200000@812\nep/main.mp4\n"
        "#EXTINF:6,\n#EXT-X-BYTERANGE:1180000\nep/main.mp4\n"
        "#EXTINF:6,\n#EXT-X-BYTERANGE:1210000\nep/main.mp4\n"
        "#EXTINF:6,\n#EXT-X-BYTERANGE:1190000@3590812\nep/main.mp4\n"
        "#EXTINF:3,\n#EXT-X-BYTERANGE:600000\nep/main.mp4\n#EXT-X-ENDLIST\n")

    def test_offsets_continue_after_previous_subrange(self):
        out = parse(self.PLAYLIST)
        self.assertEqual([s["byterange"] for s in out["segments"]],
                         [[812, 1200000],
                          [1200812, 1180000],
                          [2380812, 1210000],
                          [3590812, 1190000],
                          [4780812, 600000]])

    def test_implicit_offset_requires_same_resource(self):
        bad = ("#EXTM3U\n#EXTINF:6,\n#EXT-X-BYTERANGE:100@0\na.mp4\n"
               "#EXTINF:6,\n#EXT-X-BYTERANGE:200\nb.mp4\n")
        with self.assertRaises(HlsError):
            parse(bad)

    def test_whole_resource_has_null_byterange(self):
        out = parse("#EXTM3U\n#EXTINF:6,\na.ts\n")
        self.assertIsNone(out["segments"][0]["byterange"])

    def test_map_byterange(self):
        out = parse('#EXTM3U\n#EXT-X-MAP:URI="init.mp4",BYTERANGE="812@0"\n'
                    "#EXTINF:6,\na.ts\n")
        self.assertEqual(out["segments"][0]["map"],
                         {"uri": "init.mp4", "byterange": [0, 812]})


class GapTests(unittest.TestCase):
    PLAYLIST = ("#EXTM3U\n#EXT-X-TARGETDURATION:7\n"
                "#EXTINF:6,\na.ts\n"
                "#EXT-X-GAP\n#EXTINF:5,\nmissing.ts\n"
                "#EXTINF:4,\nb.ts\n#EXT-X-ENDLIST\n")

    def test_gap_flag_and_counts(self):
        out = parse(self.PLAYLIST)
        self.assertEqual([s["gap"] for s in out["segments"]], [False, True, False])
        self.assertEqual(out["gaps"], 1)

    def test_gap_excluded_from_playable_but_kept_in_duration(self):
        out = parse(self.PLAYLIST)
        self.assertEqual(out["duration"], "15")
        self.assertEqual(out["playable_duration"], "10")


class MasterTests(unittest.TestCase):
    PLAYLIST = (
        "#EXTM3U\n"
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English",URI="audio/en.m3u8"\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=5400000,CODECS="avc1.640028,mp4a.40.2",'
        'RESOLUTION=1920x1080,AUDIO="aud"\n'
        "video/1080p/index.m3u8\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=600000,CODECS="mp4a.40.2",AUDIO="aud"\n'
        "audio/only.m3u8\n")

    def test_quoted_codecs_keep_all_values(self):
        out = parse(self.PLAYLIST)
        self.assertEqual(out["variants"][0]["codecs"], "avc1.640028,mp4a.40.2")
        self.assertEqual(out["variants"][1]["codecs"], "mp4a.40.2")

    def test_rendition_count(self):
        self.assertEqual(parse(self.PLAYLIST)["renditions"], 1)


class UriTests(unittest.TestCase):
    def test_relative_parent_and_root_paths(self):
        out = parse("#EXTM3U\n#EXTINF:6,\n../ads/slot.ts\n"
                    "#EXTINF:6,\n/radio/live.ts\n", BASE)
        self.assertEqual(out["segments"][0]["uri"],
                         "https://cdn.example/ads/slot.ts")
        self.assertEqual(out["segments"][1]["uri"],
                         "https://cdn.example/radio/live.ts")

    def test_plain_relative_and_absolute_uri(self):
        out = parse("#EXTM3U\n#EXTINF:6,\nep/a.ts\n"
                    "#EXTINF:6,\nhttps://other.host/x.ts\n", BASE)
        self.assertEqual(out["segments"][0]["uri"],
                         "https://cdn.example/show/ep/a.ts")
        self.assertEqual(out["segments"][1]["uri"], "https://other.host/x.ts")

    def test_without_base_uris_are_verbatim(self):
        out = parse("#EXTM3U\n#EXTINF:6,\n../ads/slot.ts\n#EXTINF:6,\n/a.ts\n")
        self.assertEqual([s["uri"] for s in out["segments"]],
                         ["../ads/slot.ts", "/a.ts"])

    def test_map_uri_resolved(self):
        out = parse('#EXTM3U\n#EXT-X-MAP:URI="../ads/init.mp4"\n'
                    "#EXTINF:6,\na.ts\n", BASE)
        self.assertEqual(out["segments"][0]["map"]["uri"],
                         "https://cdn.example/ads/init.mp4")

    def test_master_variant_uri_resolved(self):
        out = parse('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1,CODECS="avc1,mp4a"\n'
                    "/radio/index.m3u8\n", BASE)
        self.assertEqual(out["variants"][0]["uri"],
                         "https://cdn.example/radio/index.m3u8")


class KeyTests(unittest.TestCase):
    def test_explicit_iv(self):
        out = parse('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k.bin",'
                    "IV=0X0123456789ABCDEF0123456789ABCDEF\n"
                    "#EXTINF:6,\na.ts\n")
        self.assertEqual(out["segments"][0]["key"], "AES-128")
        self.assertEqual(out["segments"][0]["iv"],
                         "0x0123456789abcdef0123456789abcdef")

    def test_derived_iv_uses_sequence_number(self):
        out = parse("#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:7\n"
                    '#EXT-X-KEY:METHOD=AES-128,URI="k.bin"\n'
                    "#EXTINF:6,\na.ts\n#EXTINF:6,\nb.ts\n")
        self.assertEqual(out["segments"][0]["iv"],
                         "0x00000000000000000000000000000007")
        self.assertEqual(out["segments"][1]["iv"],
                         "0x00000000000000000000000000000008")

    def test_method_none_clears_key(self):
        out = parse("#EXTM3U\n"
                    '#EXT-X-KEY:METHOD=AES-128,URI="k.bin"\n'
                    "#EXTINF:6,\na.ts\n"
                    "#EXT-X-KEY:METHOD=NONE\n"
                    "#EXTINF:6,\nb.ts\n")
        self.assertEqual(out["segments"][0]["key"], "AES-128")
        self.assertIsNone(out["segments"][1]["key"])
        self.assertIsNone(out["segments"][1]["iv"])

    def test_non_aes128_has_no_iv(self):
        out = parse('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="k.bin"\n'
                    "#EXTINF:6,\na.ts\n")
        self.assertEqual(out["segments"][0]["key"], "SAMPLE-AES")
        self.assertIsNone(out["segments"][0]["iv"])


class StructureTests(unittest.TestCase):
    def test_segment_without_extinf_is_rejected(self):
        with self.assertRaises(HlsError):
            parse("#EXTM3U\na.ts\n")

    def test_discontinuity_flags(self):
        out = parse("#EXTM3U\n#EXTINF:6,\na.ts\n"
                    "#EXT-X-DISCONTINUITY\n#EXTINF:6,\nb.ts\n")
        self.assertEqual([s["discontinuity"] for s in out["segments"]],
                         [False, True])
        self.assertEqual(out["discontinuities"], 1)


if __name__ == "__main__":
    unittest.main()
