import unittest

from schlkit.stream import SchlError, parse


def chunk(tag, payload):
    return tag + (len(payload) + 8).to_bytes(4, "little") + payload


class BasicTests(unittest.TestCase):
    def test_unknown_codec(self):
        with self.assertRaises(SchlError):
            parse(chunk(b"SCHl", bytes([1, 9]) + (22050).to_bytes(4, "little")))

    def test_missing_header(self):
        with self.assertRaises(SchlError):
            parse(chunk(b"SCDl", b"\0\0"))


if __name__ == "__main__":
    unittest.main()
