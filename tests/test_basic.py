import os
import unittest

from rmkit.rm import RmError, parse

from files import clip, ra, rv


HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class BasicTests(unittest.TestCase):
    def test_streams(self):
        out = parse(clip(60000, ra(4, 22050, 16, 1, "28_8"), rv("RV30", 320, 240, 15), n=5))
        self.assertEqual([(s["type"], s["codec"]) for s in out["streams"]], [("audio", "28_8"), ("video", "RV30")])

    def test_packets(self):
        out = parse(clip(60000, ra(4), None, n=7))
        self.assertEqual((out["streams"][0]["packets"], out["data_chunks"], out["preroll"]), (7, 1, 3000))

    def test_duration_is_seconds(self):
        # PROP stores milliseconds; 60000 ms is exactly 60 seconds.
        out = parse(clip(60000, ra(4)))
        self.assertEqual(out["duration"], "60")
        out = parse(clip(1805000, ra(4)))
        self.assertEqual(out["duration"], "1805")
        # Fractional seconds survive as a fraction string.
        out = parse(clip(1500, ra(4)))
        self.assertEqual(out["duration"], "3/2")

    def test_ra4_and_ra5_audio(self):
        v4 = parse(clip(60000, audio=ra(4, 22050, 16, 1, "sipr")))["streams"][0]
        self.assertEqual((v4["ra_version"], v4["codec"], v4["sample_rate"], v4["bits"], v4["channels"]),
                         (4, "sipr", 22050, 16, 1))
        v5 = parse(clip(60000, audio=ra(5, 44100, 16, 2, "cook")))["streams"][0]
        self.assertEqual((v5["ra_version"], v5["codec"], v5["sample_rate"], v5["bits"], v5["channels"]),
                         (5, "cook", 44100, 16, 2))

    def test_video_fps_is_fraction(self):
        out = parse(clip(60000, audio=None, video=rv("RV40", 640, 480, 25)))["streams"][0]
        self.assertEqual((out["width"], out["height"], out["fps"], out["codec"]), (640, 480, "25", "RV40"))
        out = parse(clip(60000, audio=None, video=rv("RV30", 320, 240, 15)))["streams"][0]
        self.assertEqual(out["fps"], "15")

    def test_content_descriptor_strings(self):
        out = parse(clip(60000, ra(4), title="Morning Talk", author="Radio 2"))
        self.assertEqual((out["title"], out["author"], out["copyright"], out["comment"]),
                         ("Morning Talk", "Radio 2", "(c) 1999", ""))

    def test_fileinfo_entry_is_not_a_stream(self):
        out = parse(clip(60000, ra(4), rv(), fileinfo=True, n=3))
        self.assertEqual(len(out["streams"]), 2)
        self.assertEqual([s["mime"] for s in out["streams"]],
                         ["audio/x-pn-realaudio", "video/x-pn-realvideo"])

    def test_split_data_and_index(self):
        out = parse(clip(60000, ra(4), rv(), n=10, split=True, index=True))
        self.assertEqual(out["data_chunks"], 2)
        self.assertTrue(out["indexed"])
        # Packets and keyframes are counted across every DATA chunk.
        self.assertEqual([s["packets"] for s in out["streams"]], [10, 10])
        self.assertEqual(sum(s["keyframes"] for s in out["streams"]), 2)

    def test_version_one_packet_headers(self):
        out = parse(clip(60000, ra(4), n=4, pver=1))
        s = out["streams"][0]
        self.assertEqual(s["packets"], 4)
        # v1 headers are 14 bytes, so the 40-byte payload stays 40 bytes.
        self.assertEqual(s["payload_bytes"], 4 * 40)
        # Video keyframes must still be found with the flags byte at offset 13.
        out = parse(clip(60000, None, rv(), n=10, pver=1))
        self.assertEqual(out["streams"][0]["keyframes"], 2)

    def test_multi_rate_stream(self):
        out = parse(clip(60000, audio=ra(5), multi=2, n=2))
        s = out["streams"][0]
        self.assertEqual(s["rates"], 3)
        # The first encoding still supplies the audio fields.
        self.assertEqual((s["codec"], s["sample_rate"], s["channels"]), ("cook", 44100, 2))

    def test_prop_flags(self):
        out = parse(clip(60000, ra(4)))
        self.assertFalse(out["live"])
        self.assertTrue(out["downloadable"])

    def test_real_examples(self):
        with open(os.path.join(HERE, "examples", "concert_1999.rm"), "rb") as fh:
            concert = parse(fh.read())
        self.assertEqual(concert["duration"], "21649/4")
        audio, video = concert["streams"]
        self.assertEqual((audio["type"], audio["codec"], audio["sample_rate"], audio["channels"]),
                         ("audio", "cook", 44100, 2))
        self.assertEqual((video["type"], video["codec"], video["fps"]), ("video", "RV40", "25"))
        self.assertEqual(concert["title"], "Live in Berlin")
        with open(os.path.join(HERE, "examples", "radio_talk.ra"), "rb") as fh:
            talk = parse(fh.read())
        audio = talk["streams"][0]
        self.assertEqual(talk["duration"], "1805")
        self.assertEqual((audio["codec"], audio["ra_version"], audio["sample_rate"]), ("sipr", 4, 22050))
        self.assertEqual(talk["title"], "Morning Talk")

    def test_bad_magic(self):
        with self.assertRaises(RmError):
            parse(b"RIFF" + bytes(40))

