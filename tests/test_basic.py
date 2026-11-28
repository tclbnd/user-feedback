import unittest

from vbkit.vb import VbError, parse


def movie(w, h, packets):
    return b"VBMOVIE\0" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + len(packets).to_bytes(2, "little") \
        + b"".join(len(p).to_bytes(4, "little") + p for p in packets)


class BasicTests(unittest.TestCase):
    def test_fill_frame(self):
        video = bytes([0xAA, 1, 2, 3, 4])
        pkt = (0x08).to_bytes(2, "little") + (len(video) + 4).to_bytes(4, "little") + video
        out = parse(movie(8, 8, [pkt]))
        self.assertEqual(out["frames"][0]["ok"], True)

    def test_bad_size(self):
        with self.assertRaises(VbError):
            parse(movie(6, 8, []))


if __name__ == "__main__":
    unittest.main()
