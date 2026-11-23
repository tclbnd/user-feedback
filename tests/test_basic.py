import unittest

from mlpkit.mlp import parse

from units import unit


class BasicTests(unittest.TestCase):
    def test_format(self):
        out = parse(unit(60, True) + unit(60))
        self.assertEqual((out["format"], out["sample_rate"], out["substreams"]), ("TrueHD", 48000, 2))

    def test_garbage_before_sync(self):
        self.assertEqual(parse(bytes(5) + unit(60, True))["skipped_bytes"], 5)
