import unittest

from fitskit.fits import FitsError, parse


def primary(*cards):
    text = "".join(c.ljust(80) for c in cards) + "END".ljust(80)
    return (text + " " * (-len(text) % 2880)).encode("ascii")


class BasicTests(unittest.TestCase):
    def test_empty_primary(self):
        out = parse(primary("SIMPLE  =                    T", "BITPIX  =                    8", "NAXIS   =                    0"))
        self.assertEqual((out["hdus"], out["hdu"][0]["shape"], out["hdu"][0]["data_bytes"]), (1, [], 0))

    def test_not_fits(self):
        with self.assertRaises(FitsError):
            parse(b"\x89PNG" + bytes(3000))
