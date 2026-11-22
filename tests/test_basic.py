import unittest

from psfkit.psf import parse

from rips import psf


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(psf(tags=[("title", "Intro"), ("game", "Quest")]))
        self.assertEqual((out["system"], out["program_bytes"], out["title"], out["game"]), ("PlayStation", 4096, "Intro", "Quest"))

    def test_plain_length(self):
        self.assertEqual(parse(psf(tags=[("length", "95")]))["length"], "95")
