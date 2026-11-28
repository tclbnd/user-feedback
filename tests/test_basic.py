import unittest

from dfakit.dfa import DfaError, parse


def movie(w, h, frames):
    return b"DFAMOVIE" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + bytes(2) + len(frames).to_bytes(2, "little") \
        + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


def chunk(kind, body):
    return b"CHNK" + len(body).to_bytes(4, "little") + kind.to_bytes(4, "little") + body


class BasicTests(unittest.TestCase):
    def test_copy_frame(self):
        out = parse(movie(4, 2, [chunk(2, bytes(range(8))) + chunk(0, b"")]))
        self.assertEqual(out["frames"][0]["ok"], True)

    def test_bad_magic(self):
        with self.assertRaises(DfaError):
            parse(b"FLICFILE" + bytes(8))


if __name__ == "__main__":
    unittest.main()
