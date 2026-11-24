import unittest

from rtpkit.rtp import parse

from caps import one_packet


class BasicTests(unittest.TestCase):
    def test_one_stream(self):
        out = parse(one_packet())
        self.assertEqual((out["link_type"], len(out["streams"]), out["streams"][0]["ssrc"]), (1, 1, "cafe0001"))

    def test_packets(self):
        s = parse(one_packet())["streams"][0]
        self.assertEqual((s["packets"], s["lost"]), (1, 0))
