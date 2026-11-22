import unittest

from cafkit.caf import parse

from cafs import audio, caf, desc


class BasicTests(unittest.TestCase):
    def test_desc(self):
        out = parse(caf(desc(48000.0, b"lpcm", 0, 4, 1, 2, 16)))
        self.assertEqual((out["sample_rate"], out["format"], out["channels"], out["bits"]), (48000.0, "lpcm", 2, 16))

    def test_chunks(self):
        out = parse(caf(desc()))
        self.assertEqual(out["chunks"], ["desc"])
