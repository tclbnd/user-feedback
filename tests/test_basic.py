import unittest

from dolbyscan import compliance
from dolbyscan.syncframe import frame_words


class BasicTests(unittest.TestCase):
    def test_frame_words_48k(self):
        self.assertEqual(frame_words(0, 30), 896)
        self.assertEqual(frame_words(0, 0), 64)

    def test_compliance(self):
        s = {"sample_rate": 48000, "channels": 6, "dialnorm": -27, "bitrate": 448000}
        self.assertEqual(compliance.check(s, {"channels": 6, "dialnorm": -24}), ["dialnorm"])


if __name__ == "__main__":
    unittest.main()
