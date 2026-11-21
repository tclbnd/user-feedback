import unittest

from qoakit.qoa import parse

from files import song


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(song(5120, ch=1, rate=44100))
        self.assertEqual((out["channels"], out["sample_rate"], out["samples"], out["frames"]), (1, 44100, 5120, 1))

    def test_duration(self):
        self.assertEqual(parse(song(10240, ch=1, rate=48000))["duration"], "16/75")
