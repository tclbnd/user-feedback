import unittest

from burstkit.burst import parse

from captures import ac3_burst, wav


class BasicTests(unittest.TestCase):
    def test_rate(self):
        self.assertEqual(parse(wav(ac3_burst()))["sample_rate"], 48000)

    def test_ac3(self):
        s = parse(wav(ac3_burst() * 3))["streams"]["0/AC-3"]
        self.assertEqual((s["bursts"], s["payload_bytes"]), (3, 3840))
