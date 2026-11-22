import unittest

from smkkit.smk import parse

from videos import smk


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(smk(frames=4, width=640, height=480, tracks=()))
        self.assertEqual((out["version"], out["width"], out["height"], out["ring_frame"]), ("SMK2", 640, 480, False))

    def test_version(self):
        self.assertEqual(parse(smk(frames=2, tracks=(), version=b"SMK4"))["version"], "SMK4")
