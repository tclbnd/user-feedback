import unittest

from asfkit.asf import parse

from headers import asf, audio_stream, file_props


class BasicTests(unittest.TestCase):
    def test_file_properties(self):
        out = parse(asf(file_props(60, 0, packets=100, packet=3200), audio_stream(1)))
        self.assertEqual((out["preroll"], out["packets"], out["packet_size"], out["seekable"]), (0, 100, 3200, True))

    def test_audio_stream(self):
        s = parse(asf(file_props(), audio_stream(1, 0x0161, 2, 44100, 16)))["streams"][0]
        self.assertEqual((s["number"], s["type"], s["codec"], s["channels"], s["sample_rate"]), (1, "audio", "0x0161", 2, 44100))
