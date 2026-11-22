import unittest

from vgmkit.vgm import parse

from logs import commands, vgm


class BasicTests(unittest.TestCase):
    def test_total(self):
        out = parse(vgm(body=commands(20, "61")))
        self.assertEqual((out["total_samples"], out["loop"]), (14000, None))

    def test_chips(self):
        out = parse(vgm(chips={"SN76489": 3579545, "YM2612": 7670453}, body=commands(2)))
        self.assertEqual(sorted(out["chips"]), ["SN76489", "YM2612"])
