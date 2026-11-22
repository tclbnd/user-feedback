import unittest

from s3mkit.s3m import parse

from modules import ins, module


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(module("menu", orders=(0,), patterns=[None]))
        self.assertEqual((out["title"], out["tracker"], out["patterns"]), ("menu", "Scream Tracker", 1))

    def test_sample(self):
        out = parse(module(orders=(0,), instruments=[ins("kick", 300)], patterns=[None]))
        s = out["samples"][0]
        self.assertEqual((s["index"], s["name"], s["frames"]), (1, "kick", 300))
