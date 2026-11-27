import unittest

from qoikit.qoi import QoiError, parse

END = b"\0" * 7 + b"\1"


def image(w, h, chunks, channels=4):
    return b"qoif" + w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes([channels, 0]) + chunks + END


class BasicTests(unittest.TestCase):
    def test_index_zero(self):
        out = parse(image(2, 1, b"\x00\x00"))
        self.assertEqual(out["pixels"], 2)
        self.assertEqual(out["ops"]["index"], 2)

    def test_bad_magic(self):
        with self.assertRaises(QoiError):
            parse(b"qoix" + bytes(18))


if __name__ == "__main__":
    unittest.main()
