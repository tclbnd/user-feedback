import unittest

from rtpkit.rtp import parse

import caps
from caps import one_packet


def stream(*args, **kwargs):
    """Parse a single-RTP-stream capture and return that stream."""
    return parse(one_packet(*args, **kwargs))["streams"][0]


class BasicTests(unittest.TestCase):
    def test_one_stream(self):
        out = parse(one_packet())
        self.assertEqual((out["link_type"], len(out["streams"]), out["streams"][0]["ssrc"]), (1, 1, "cafe0001"))

    def test_packets(self):
        s = stream()
        self.assertEqual((s["packets"], s["lost"]), (1, 0))

    def test_not_a_capture(self):
        with self.assertRaises(ValueError):
            parse(b"\x00" * 64)

