import unittest

from roqkit.roq import RoqError, parse

from movies import empty


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(empty())
        self.assertEqual((out["fps"], out["frames"], out["audio_channels"]), (30, 0, None))

    def test_not_roq(self):
        with self.assertRaises(RoqError):
            parse(b"RIFF" + bytes(20))
