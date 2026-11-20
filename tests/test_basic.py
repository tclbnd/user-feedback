import unittest

from flacmeta import catalog, parse


class BasicTests(unittest.TestCase):
    def test_streaminfo_rate(self):
        bits = (44100 << 44) | (1 << 41) | (15 << 36) | 1000
        body = (4096).to_bytes(2, "big") * 2 + bytes(6) + bits.to_bytes(8, "big") + bytes(16)
        info = parse.streaminfo(body)
        self.assertEqual((info["sample_rate"], info["min_blocksize"], info["total_samples"]), (44100, 4096, 1000))

    def test_missing(self):
        self.assertEqual(catalog.missing_tags({"TITLE": "x", "ARTIST": "y"}), ["ALBUM", "ISRC"])


if __name__ == "__main__":
    unittest.main()
