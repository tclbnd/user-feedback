import unittest

from cmvkit.cmv import CmvError, parse


def movie(packets):
    return b"CMVMOVIE" + len(packets).to_bytes(2, "little") + b"".join(len(p).to_bytes(4, "little") + p for p in packets)


def header(w, h):
    body = bytes(4) + w.to_bytes(2, "little") + h.to_bytes(2, "little") + bytes(8)
    return b"MVIh" + (8 + len(body)).to_bytes(4, "little") + body


class BasicTests(unittest.TestCase):
    def test_key_frame(self):
        out = parse(movie([header(4, 4) + bytes(8) + b"\x00\x00" + bytes(range(16))]))
        self.assertEqual(out["width"], 4)
        self.assertEqual(out["frames"][0]["key"], True)

    def test_bad_magic(self):
        with self.assertRaises(CmvError):
            parse(b"MVIMOVIE" + bytes(4))


if __name__ == "__main__":
    unittest.main()
