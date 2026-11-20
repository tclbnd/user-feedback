import unittest

from spsinfo import ladder
from spsinfo.bits import BitReader


class BasicTests(unittest.TestCase):
    def test_exp_golomb(self):
        r = BitReader(bytes([0b10100110, 0b01000000]))
        self.assertEqual([r.ue(), r.ue(), r.ue(), r.ue()], [0, 1, 2, 3])

    def test_ladder(self):
        self.assertEqual(ladder.rungs(1280, 720), ["1280x720", "960x540", "640x360"])


if __name__ == "__main__":
    unittest.main()
