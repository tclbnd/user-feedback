import unittest

from vmdkit.vmd import VmdError, parse


def frames_file(w, h, recs):
    return b"VMDFRAME" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + bytes(4) + len(recs).to_bytes(2, "little") \
        + b"".join(len(r).to_bytes(4, "little") + r for r in recs)


class BasicTests(unittest.TestCase):
    def test_no_frames(self):
        out = parse(frames_file(8, 4, []))
        self.assertEqual(out["frames"], [])

    def test_empty_picture(self):
        with self.assertRaises(VmdError):
            parse(frames_file(0, 4, []))


if __name__ == "__main__":
    unittest.main()
