import unittest

from eakit.adpcm import EaError, parse


def stream(ch, rate, packets):
    return b"EAADPCM\0" + bytes([ch, 0]) + rate.to_bytes(4, "little") + len(packets).to_bytes(2, "little") \
        + b"".join(len(p).to_bytes(4, "little") + p for p in packets)


class BasicTests(unittest.TestCase):
    def test_empty_stream(self):
        out = parse(stream(2, 22050, []))
        self.assertEqual(out["samples"], 0)
        self.assertEqual(out["packets"], [])

    def test_bad_channels(self):
        with self.assertRaises(EaError):
            parse(stream(3, 22050, []))


if __name__ == "__main__":
    unittest.main()
