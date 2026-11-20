import unittest

from telcodec import g711, vad


class BasicTests(unittest.TestCase):
    def test_ulaw_decode(self):
        self.assertEqual(g711.ulaw_decode(0xFF), 0)
        self.assertEqual(g711.ulaw_decode(0x80), 32124)

    def test_vad(self):
        speech = [3000, -3000] * 80
        self.assertEqual(vad.active([0] * 160 + speech + [0] * 800), [False, True, True, True, True, False, False])


if __name__ == "__main__":
    unittest.main()
