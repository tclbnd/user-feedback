import unittest

from xmkit.xm import parse

from modules import instrument, module, pattern, sample


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(module("intro", channels=4, patterns=[pattern()]))
        self.assertEqual((out["name"], out["version"], out["channels"], out["patterns"]), ("intro", "1.04", 4, [64]))

    def test_instrument(self):
        out = parse(module(patterns=[pattern()], instruments=[instrument("lead", [sample("a", 200)])]))
        s = out["instruments"][0]["samples"][0]
        self.assertEqual((s["name"], s["frames"], s["bits"]), ("a", 200, 8))
