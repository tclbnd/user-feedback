import unittest

from dpcmkit.dpcm import DpcmError, parse


def pak(tag, channels, packets):
    return b"DPCMPAK\0" + tag + bytes([channels]) + (22050).to_bytes(4, "little") + bytes([len(packets)]) \
        + b"".join(len(p).to_bytes(4, "little") + p for p in packets)


class BasicTests(unittest.TestCase):
    def test_sol16_mono(self):
        out = parse(pak(b"SOL3", 1, [bytes([0, 0, 0, 0])]))
        self.assertEqual(out["frames"], 4)
        self.assertEqual(out["bits"], 16)

    def test_unknown_tag(self):
        with self.assertRaises(DpcmError):
            parse(pak(b"ABCD", 1, []))


if __name__ == "__main__":
    unittest.main()
