import struct
import unittest

from adwav.msadpcm import AdpcmError, parse

COEFS = [(256, 0), (512, -256), (0, 0), (192, 64),
         (240, 0), (460, -208), (392, -232)]


def fmt(channels=1, rate=8000, align=8, spb=4, coefs=None):
    coefs = COEFS if coefs is None else coefs
    body = struct.pack("<HHIIHHHHH", 2, channels, rate,
                       rate * align * channels // spb, align, 4,
                       26, spb, len(coefs))
    return body + b"".join(struct.pack("<hh", *c) for c in coefs)


def mono_block(pred=0, delta=16, s1=10, s2=20, nibbles=(1, 2)):
    out = bytes([pred]) + struct.pack("<Hhh", delta, s1, s2)
    for i in range(0, len(nibbles), 2):
        out += bytes([(nibbles[i] << 4) | nibbles[i + 1]])
    return out


def riff(*chunks):
    body = b""
    for cid, payload in chunks:
        body += cid + struct.pack("<I", len(payload)) + payload
        if len(payload) & 1:
            body += b"\x00"
    return b"RIFF" + struct.pack("<I", len(body)) + b"WAVE" + body


def tiny():
    blk = bytes([0]) + struct.pack("<hhh", 16, 100, 100) + bytes(9)
    return riff((b"fmt ", fmt(align=16, spb=20)), (b"data", blk))


class BasicTests(unittest.TestCase):
    def test_silent_block(self):
        out = parse(tiny())
        self.assertEqual(out["channels"], 1)
        self.assertEqual(out["frames"], 20)
        self.assertEqual(out["peak"], 100)

    def test_not_wave(self):
        with self.assertRaises(AdpcmError):
            parse(b"RIFX" + bytes(40))

    def test_header_sample_order_is_s2_then_s1(self):
        # s2 is the older sample, s1 the most recent: playback emits s2, s1
        out = parse(riff((b"fmt ", fmt()),
                         (b"data", mono_block(s1=1000, s2=800))))
        self.assertEqual(out["head"][0][:2], [800, 1000])

    def test_nibbles_high_first_and_delta_floor(self):
        # byte 0x3B: high nibble 3 then low nibble 11 (-5)
        blk = bytes([0]) + struct.pack("<Hhh", 100, 1000, 800) + b"\x3b"
        out = parse(riff((b"fmt ", fmt()), (b"data", blk)))
        self.assertEqual(out["head"][0], [800, 1000, 1300, 855])
        # delta may never fall below 16, so signal survives a quiet run
        quiet = mono_block(delta=16, s1=100, s2=100, nibbles=(0, 0))
        loud = mono_block(delta=16, s1=100, s2=100, nibbles=(8, 0))
        out = parse(riff((b"fmt ", fmt()), (b"data", quiet + loud)))
        # second block: [s2=100, s1=100, -28 (nibble 8, delta floored 16)]
        self.assertEqual(out["last"], [-28])

    def test_stereo_header_interleaved_by_field(self):
        hdr = bytes([1, 4]) + struct.pack("<HH", 1000, 2000)
        hdr += struct.pack("<hh", 1200, -500)
        hdr += struct.pack("<hh", -300, 700) + b"\x5a"
        out = parse(riff((b"fmt ", fmt(channels=2, align=16)),
                         (b"data", hdr)))
        self.assertEqual(out["head"][0], [-300, 1200, 7700])
        self.assertEqual(out["head"][1], [700, -500, -12469])

    def test_odd_list_chunk_pad_byte(self):
        info = b"INFO" + b"ISFT" + struct.pack("<I", 5) + b"demo" + b"\x00"
        self.assertEqual(len(info), 17)  # odd size -> one pad byte follows
        out = parse(riff((b"LIST", info), (b"fmt ", fmt()),
                         (b"data", mono_block())))
        self.assertEqual(out["frames"], 4)

    def test_short_last_block_counts(self):
        full = mono_block(s1=500, s2=400)
        short = (mono_block(s1=10, s2=20, nibbles=(7, 3)))[:8]
        out = parse(riff((b"fmt ", fmt()), (b"data", full + short)))
        self.assertEqual(out["blocks"], 2)
        self.assertEqual(out["frames"], 8)

    def test_unsigned_initial_delta(self):
        # delta 60000 must not be sign-extended into a negative value
        blk = mono_block(delta=60000, s1=-30000, s2=30000)
        out = parse(riff((b"fmt ", fmt()), (b"data", blk)))
        self.assertEqual(out["frames"], 4)

    def test_bad_predictor_and_missing_fmt(self):
        bad = bytes([7]) + struct.pack("<Hhh", 16, 1, 2) + b"\x00"
        with self.assertRaises(AdpcmError):
            parse(riff((b"fmt ", fmt()), (b"data", bad)))
        with self.assertRaises(AdpcmError):
            parse(riff((b"data", b"\x00" * 8)))

    def test_clamp_to_sixteen_bits(self):
        blk = mono_block(delta=40000, s1=32767, s2=32767, nibbles=(7, 7))
        out = parse(riff((b"fmt ", fmt()), (b"data", blk)))
        self.assertLessEqual(max(abs(v) for v in out["head"][0]), 32768)

    def test_coefficients_read_from_fmt(self):
        custom = [(256, 0), (512, -256), (128, 128)]
        hdr = bytes([2, 0]) + struct.pack("<HH", 500, 250)
        hdr += struct.pack("<hh", 300, -100)
        hdr += struct.pack("<hh", 200, -50) + b"\x4c\x12"
        out = parse(riff((b"fmt ", fmt(channels=2, align=16, coefs=custom)),
                         (b"data", hdr)))
        self.assertEqual(out["channels"], 2)
        self.assertEqual(out["blocks"], 1)


if __name__ == "__main__":
    unittest.main()
