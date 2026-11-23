import unittest

from cdgkit.cdg import parse

from packs import preset, tile


class BasicTests(unittest.TestCase):
    def test_counts(self):
        out = parse(preset() + tile(1, 1, 0, 1) + bytes(24))
        self.assertEqual((out["packets"], out["graphics_packets"]), (3, 2))

    def test_tile_pixels(self):
        out = parse(preset() + tile(2, 3, 0, 5))
        self.assertEqual(out["pixels"]["5"], 72)
