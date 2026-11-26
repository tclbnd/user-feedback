import struct
import unittest

from adwav.msadpcm import AdpcmError, parse


def tiny():
    coefs = [(256, 0), (512, -256), (0, 0), (192, 64), (240, 0), (460, -208), (392, -232)]
    fmt = struct.pack("<HHIIHHHHH", 2, 1, 8000, 4000, 16, 4, 32, 20, 7) + b"".join(struct.pack("<hh", *c) for c in coefs)
    blk = bytes([0]) + struct.pack("<hhh", 16, 100, 100) + bytes(9)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(blk)) + blk
    return b"RIFF" + struct.pack("<I", len(body)) + body


class BasicTests(unittest.TestCase):
    def test_silent_block(self):
        out = parse(tiny())
        self.assertEqual(out["channels"], 1)
        self.assertEqual(out["frames"], 20)
        self.assertEqual(out["peak"], 100)

    def test_not_wave(self):
        with self.assertRaises(AdpcmError):
            parse(b"RIFX" + bytes(40))


if __name__ == "__main__":
    unittest.main()
