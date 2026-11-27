import unittest

from filmkit.film import FilmError, parse


class BasicTests(unittest.TestCase):
    def test_not_film(self):
        with self.assertRaises(FilmError):
            parse(b"MOOV" + bytes(60))

    def test_no_streams(self):
        fdsc = b"FDSC" + (32).to_bytes(4, "big") + b"none" + bytes(20)
        stab = b"STAB" + (16).to_bytes(4, "big") + (600).to_bytes(4, "big") + bytes(4)
        data = b"FILM" + (64).to_bytes(4, "big") + (1).to_bytes(4, "big") + bytes(4) + fdsc + stab
        with self.assertRaises(FilmError):
            parse(data)


if __name__ == "__main__":
    unittest.main()
