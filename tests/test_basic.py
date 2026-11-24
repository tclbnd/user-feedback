import unittest

from dlskit.dls import parse

from banks import bank


class BasicTests(unittest.TestCase):
    def test_waves(self):
        out = parse(bank(250))
        self.assertEqual((out["waves"], out["wave_frames"]), (1, 250))

    def test_instrument(self):
        inst = parse(bank())["instruments"][0]
        self.assertEqual((inst["program"], len(inst["regions"]), inst["regions"][0]["keys"]), (5, 1, [0, 127]))
