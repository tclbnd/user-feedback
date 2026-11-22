import unittest

from asfkit.asf import parse

from headers import (asf, audio_stream, content, ext_content, ext_stream_props, extension, file_props, metadata,
                     video_stream)


class BasicTests(unittest.TestCase):
    def test_file_properties(self):
        out = parse(asf(file_props(60, 0, packets=100, packet=3200), audio_stream(1)))
        self.assertEqual((out["preroll"], out["packets"], out["packet_size"], out["seekable"]), (0, 100, 3200, True))

    def test_audio_stream(self):
        s = parse(asf(file_props(), audio_stream(1, 0x0161, 2, 44100, 16)))["streams"][0]
        self.assertEqual((s["number"], s["type"], s["codec"], s["channels"], s["sample_rate"]), (1, "audio", "0x0161", 2, 44100))

    def test_duration_excludes_preroll(self):
        out = parse(asf(file_props(60, 3000), audio_stream(1)))
        self.assertEqual(out["duration"], "60")

    def test_broadcast_file(self):
        out = parse(asf(file_props(broadcast=True), audio_stream(1)))
        self.assertIsNone(out["duration"])
        self.assertIsNone(out["packets"])
        self.assertTrue(out["broadcast"])

    def test_encrypted_stream_number(self):
        s = parse(asf(file_props(), audio_stream(2, encrypted=True)))["streams"][0]
        self.assertEqual((s["number"], s["encrypted"]), (2, True))

    def test_content_description_is_utf16(self):
        out = parse(asf(file_props(), content(title="Café", author="Band", rating="PG")))
        self.assertEqual((out["title"], out["author"]), ("Café", "Band"))

    def test_typed_tag_values(self):
        out = parse(asf(file_props(), ext_content([
            ("WM/Genre", 0, "Jazz"),
            ("IsVBR", 2, 1),
            ("WM/TrackNumber", 3, 7),
            ("WM/Duration", 4, 1 << 40),
            ("WM/Track", 5, 3),
            ("WM/Picture", 1, b"\xde\xad"),
        ])))
        self.assertEqual(out["tags"]["WM/Genre"], "Jazz")
        self.assertIs(out["tags"]["IsVBR"], True)
        self.assertEqual(out["tags"]["WM/TrackNumber"], 7)
        self.assertEqual(out["tags"]["WM/Duration"], 1 << 40)
        self.assertEqual(out["tags"]["WM/Track"], 3)
        self.assertEqual(out["tags"]["WM/Picture"], "dead")

    def test_repeated_tags_keep_file_order(self):
        out = parse(asf(file_props(), ext_content([("WM/Genre", 0, "Jazz")]),
                        extension(metadata([("WM/Genre", 0, "Live")], library=True))))
        self.assertEqual(out["tags"]["WM/Genre"], ["Jazz", "Live"])

    def test_metadata_in_header_extension(self):
        out = parse(asf(file_props(), audio_stream(1),
                        extension(metadata([("IsVBR", 2, 1), ("WM/SharedUserRating", 3, 75)]))))
        self.assertEqual(out["tags"], {"IsVBR": True, "WM/SharedUserRating": 75})

    def test_video_frame_rate(self):
        out = parse(asf(file_props(), video_stream(2),
                        extension(ext_stream_props(2, 333667))))
        s = out["streams"][0]
        self.assertEqual((s["type"], s["codec"], s["frame_rate"]), ("video", "WMV3", "10000000/333667"))

    def test_video_frame_rate_absent(self):
        s = parse(asf(file_props(), video_stream(2)))["streams"][0]
        self.assertIsNone(s["frame_rate"])


if __name__ == "__main__":
    unittest.main()
