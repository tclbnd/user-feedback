import unittest

from spckit.spc import parse

from rips import spc


class BasicTests(unittest.TestCase):
    def test_tag(self):
        out = parse(spc(title="Opening", game="Quest"))
        self.assertEqual((out["title"], out["game"], out["id666_format"]), ("Opening", "Quest", "text"))

    def test_seconds(self):
        out = parse(spc(secs=95, emulator=1))
        self.assertEqual((out["play_seconds"], out["emulator"], out["pc"]), (95, "ZSNES", "0430"))
