import struct
import unittest

from ltckit.ltc import decode

import takes
from takes import biphase, frame_bits, ltc_wav, tc_add


def make_wav(chans, rate=48000, bits=16, extensible=False, odd_chunk=False):
    ch = len(chans)
    n = len(chans[0])
    width = bits // 8
    raw = bytearray()
    for i in range(n):
        for c in range(ch):
            v = int(chans[c][i])
            if width == 1:
                raw.append((v >> 8) + 128)
            elif width == 2:
                raw += struct.pack("<h", v)
            else:
                raw += (v * 256).to_bytes(3, "little", signed=True)
    if extensible:
        fmt = struct.pack("<HHIIHHHHI", 0xFFFE, ch, rate, rate * ch * width, ch * width, bits, 22, bits, 0)
        fmt += struct.pack("<H", 1) + bytes.fromhex("000000001000800000aa00389b71")
    else:
        fmt = struct.pack("<HHIIHH", 1, ch, rate, rate * ch * width, ch * width, bits)
    extra = (b"LIST" + struct.pack("<I", 5) + b"INFOx" + b"\0") if odd_chunk else b""
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + extra + b"data" + struct.pack("<I", len(raw)) + bytes(raw)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def recording(start, n, fps, rate=48000, user=0, drop=False, reverse=False, bits=16,
              channels=1, lead=0.37, invert=False, dc=0, extensible=False, odd_chunk=False):
    from ltckit.ltc import next_tc
    tcs = [start]
    for _ in range(n - 1):
        tcs.append(next_tc(tcs[-1], fps, drop))     # drop-frame numbering skips 00 and 01
    stream = []
    for tc in tcs:
        stream += frame_bits(tc, drop, user)
    if reverse:
        stream = stream[::-1]
    before = frame_bits(tcs[0], drop, user)         # partial frame the recording cuts into
    if reverse:
        before = before[::-1]
    # the recording starts inside the previous frame and runs out after the last one
    stream = before[80 - int(lead * 80):] + stream + [0] * 24
    sig = [s + dc for s in biphase(stream, rate, fps, invert=invert)]
    chans = [[int(3000 * __import__("math").sin(i / 7.0)) for i in range(len(sig))]
             for _ in range(channels - 1)] + [sig]
    return make_wav(chans, rate, bits, extensible, odd_chunk)


class BasicTests(unittest.TestCase):
    def test_mono(self):
        out = decode(ltc_wav((1, 0, 0, 0), 10, 25, lead=0.1))
        self.assertEqual((out["fps"], out["frames"], out["last"]), ("25", 10, "01:00:00:09"))

    def test_rate(self):
        out = decode(ltc_wav((0, 10, 0, 0), 6, 30, 44100, lead=0.1))
        self.assertEqual((out["sample_rate"], out["fps"], out["first"]), (44100, "30", "00:10:00:00"))


class FormatTests(unittest.TestCase):
    def test_stereo_ltc_on_last_channel(self):
        out = decode(recording((1, 2, 3, 4), 12, 25, channels=2))
        self.assertEqual((out["channels"], out["frames"], out["first"], out["last"]),
                         (2, 12, "01:02:03:04", "01:02:03:15"))

    def test_24bit(self):
        out = decode(recording((1, 2, 3, 4), 12, 25, bits=24, channels=2))
        self.assertEqual((out["frames"], out["first"], out["last"]), (12, "01:02:03:04", "01:02:03:15"))

    def test_8bit(self):
        out = decode(recording((2, 0, 0, 0), 8, 30, bits=8))
        self.assertEqual((out["frames"], out["fps"], out["last"]), (8, "30", "02:00:00:07"))

    def test_extensible(self):
        out = decode(recording((3, 0, 0, 0), 6, 25, extensible=True))
        self.assertEqual((out["frames"], out["first"]), (6, "03:00:00:00"))

    def test_odd_sized_chunk_before_data(self):
        out = decode(recording((4, 0, 0, 0), 6, 25, odd_chunk=True))
        self.assertEqual((out["frames"], out["first"]), (6, "04:00:00:00"))

    def test_dc_offset_and_inverted(self):
        out = decode(recording((1, 0, 0, 0), 6, 25, dc=2000, invert=True))
        self.assertEqual((out["frames"], out["first"]), (6, "01:00:00:00"))


class TimecodeTests(unittest.TestCase):
    def test_drop_frame(self):
        out = decode(recording((9, 59, 59, 20), 40, 30, drop=True))
        self.assertEqual((out["drop_frame"], out["fps"], out["first"], out["last"], out["gaps"]),
                         (True, "30000/1001", "09:59:59;20", "10:00:00;29", 0))

    def test_drop_frame_skips_two_frames_on_odd_minute(self):
        out = decode(recording((0, 1, 59, 25), 10, 30, drop=True))
        self.assertEqual((out["first"], out["last"], out["gaps"]), ("00:01:59;25", "00:02:00;06", 0))

    def test_reverse(self):
        out = decode(recording((5, 0, 0, 20), 10, 25, reverse=True))
        self.assertEqual((out["reverse"], out["frames"], out["first"], out["last"], out["gaps"]),
                         (True, 10, "05:00:01:04", "05:00:00:20", 0))

    def test_user_bits_group8_first(self):
        out = decode(recording((1, 0, 0, 0), 5, 25, user=0x12345678))
        self.assertEqual(out["user_bits"], "12345678")

    def test_user_bits_reverse_stream(self):
        out = decode(recording((1, 0, 0, 0), 5, 25, user=0xABCD1234, reverse=True))
        self.assertEqual((out["user_bits"], out["reverse"]), ("ABCD1234", True))

    def test_partial_head_frame_not_counted(self):
        out = decode(recording((6, 0, 0, 0), 7, 25, lead=0.5))
        self.assertEqual((out["frames"], out["first"]), (7, "06:00:00:00"))

    def test_24fps(self):
        out = decode(recording((1, 0, 0, 0), 9, 24))
        self.assertEqual((out["fps"], out["last"]), ("24", "01:00:00:08"))
