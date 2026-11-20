import unittest

from oggscan import opus, retention


class BasicTests(unittest.TestCase):
    def test_head_and_toc(self):
        h = opus.head(b"OpusHead\x01\x01\x38\x01\x80\xbb\x00\x00\x00\x00\x00")
        self.assertEqual((h.channels, h.pre_skip, h.input_rate), (1, 312, 48000))
        self.assertEqual(opus.samples(bytes([(19 << 3) | 0, 0])), 960)

    def test_retention(self):
        notes = [{"id": "a", "received": "2026-09-01T10:00:00", "bytes": 10},
                 {"id": "b", "received": "2026-10-30T10:00:00", "bytes": 20},
                 {"id": "c", "received": "2026-09-01T10:00:00", "starred": True, "bytes": 30}]
        ids = retention.expired(notes, "2026-11-01T00:00:00")
        self.assertEqual(ids, ["a"])
        self.assertEqual(retention.freed_bytes(notes, ids), 10)


if __name__ == "__main__":
    unittest.main()
