import unittest

from sdpkit.sdp import parse

from descriptions import one_stream


class BasicTests(unittest.TestCase):
    def test_media(self):
        m = parse(one_stream())["media"][0]
        self.assertEqual((m["type"], m["port"], m["address"], m["payloads"][0]["encoding"]), ("audio", 5004, "10.0.0.2", "L24"))

    def test_l16_packet(self):
        self.assertEqual(parse(one_stream("L16/48000/2"))["media"][0]["packet_bytes"], 192)
