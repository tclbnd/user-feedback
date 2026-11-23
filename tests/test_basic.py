import unittest

from gpmfkit.gpmf import parse

from klv import track


class BasicTests(unittest.TestCase):
    def test_device(self):
        dev = parse(track())["devices"][0]
        self.assertEqual((dev["name"], list(dev["streams"])), ("CAMA", ["ACCL"]))

    def test_elements(self):
        st = parse(track(5))["devices"][0]["streams"]["ACCL"]
        self.assertEqual((st["elements"], st["samples"]), (3, 5))
