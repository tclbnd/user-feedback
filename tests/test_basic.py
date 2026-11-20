import unittest

from tagread import feed
from tagread.summary import summarise


def tag(frames):
    body = b"".join(fid + len(b).to_bytes(4, "big") + b"\0\0" + b for fid, b in frames)
    size = len(body)
    return b"ID3\x03\x00\x00" + bytes([(size >> 21) & 127, (size >> 14) & 127, (size >> 7) & 127, size & 127]) + body


class BasicTests(unittest.TestCase):
    def test_v23_latin1(self):
        s = summarise(tag([(b"TIT2", b"\x00Pilot"), (b"TALB", b"\x00Season One")]))
        self.assertEqual(s["text"], {"TIT2": ["Pilot"], "TALB": ["Season One"]})
        self.assertEqual(feed.feed_item(s)["title"], "Pilot")


if __name__ == "__main__":
    unittest.main()
