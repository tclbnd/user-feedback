import unittest

from muskit.mus import parse

from lumps import lump


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(lump())
        self.assertEqual((out["primary_channels"], out["instruments"], out["score_end"]), (1, [30], True))

    def test_ticks(self):
        self.assertEqual(parse(lump())["ticks"], 20)
