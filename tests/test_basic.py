import unittest

from aviscan import clips
from aviscan.riff import walk


class BasicTests(unittest.TestCase):
    def test_walk(self):
        data = b"RIFF" + (20).to_bytes(4, "little") + b"AVI " + b"JUNK" + (4).to_bytes(4, "little") + b"abcd"
        top = walk(data, 0, len(data))[0]
        self.assertEqual((top.list_type, [c.id for c in top.children]), ("AVI ", ["JUNK"]))

    def test_bounds(self):
        self.assertEqual(clips.bounds([0, 25, 50], 30, 60), (25, 60))
        self.assertEqual(clips.key_times({"rate": "25"}, [0, 25]), ["0", "1"])


if __name__ == "__main__":
    unittest.main()
