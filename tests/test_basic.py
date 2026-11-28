import unittest

from jvkit.jv import JvError, parse


def movie(w, h, packets):
    return b"JVMOVIE\0" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + len(packets).to_bytes(2, "little") \
        + b"".join(len(p).to_bytes(4, "little") + p for p in packets)


class BasicTests(unittest.TestCase):
    def test_fill_frame(self):
        video = bytes([0x40 | 0x05 >> 2, (0x05 & 3) << 6])
        out = parse(movie(8, 8, [len(video).to_bytes(4, "little") + b"\x00" + video]))
        self.assertEqual(len(out["frames"]), 1)
        self.assertEqual(out["frames"][0]["ok"], True)

    def test_bad_size(self):
        with self.assertRaises(JvError):
            parse(movie(10, 8, []))


if __name__ == "__main__":
    unittest.main()
