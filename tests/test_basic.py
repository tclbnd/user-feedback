import struct
import unittest
import zlib

from cartkit.cart import CartError, parse


def image(byte):
    return cart_png(bytes([byte]) * 0x8020)


def cart_png(mem):
    mem = bytes(mem).ljust(0x8020, b"\x00")
    px = b"".join(bytes([0xFC | (b >> 4 & 3), 0xFC | (b >> 2 & 3), 0xFC | (b & 3), 0xFC | b >> 6])
                  for b in mem)
    raw = b"".join(b"\x00" + px[y * 160 * 4:(y + 1) * 160 * 4] for y in range(205))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 160, 205, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class BasicTests(unittest.TestCase):
    def test_filled_cart(self):
        out = parse(image(0x11))
        self.assertEqual(out["gfx_histogram"][1], 128 * 128)
        self.assertEqual(out["version"], 0x11)

    def test_not_a_cart(self):
        with self.assertRaises(CartError):
            parse(b"GIF89a")


def note(pitch, waveform=0, volume=5, effect=0, custom=False):
    return pitch | waveform << 6 | volume << 9 | effect << 12 | int(custom) << 15


class LayoutTests(unittest.TestCase):
    def test_map_includes_lower_half(self):
        mem = bytearray(0x8020)
        mem[0x2000] = 5          # upper half, row 0
        mem[0x1000] = 5          # lower half, row 32
        mem[0x1fff] = 7          # lower half, last row
        out = parse(cart_png(mem))
        self.assertEqual(out["map_cells"], 3)
        self.assertEqual(out["map_tiles"], 2)

    def test_flagged_sprites(self):
        mem = bytearray(0x8020)
        mem[0x3000 + 3] = 0x80
        mem[0x3000 + 127] = 0x01
        self.assertEqual(parse(cart_png(mem))["flagged_sprites"], 2)

    def test_sfx_counts_only_audible_notes(self):
        mem = bytearray(0x8020)
        mem[0x3200:0x3206] = struct.pack("<HHH", note(10, waveform=0, effect=1),
                                         note(5, waveform=3, volume=3, effect=7, custom=True),
                                         note(60, volume=0))  # volume 0: silent
        mem[0x3200 + 0x42:0x3200 + 0x44] = bytes([2, 4])     # loop start 2, end 4
        out = parse(cart_png(mem))
        self.assertEqual(out["sfx_used"], 1)
        self.assertEqual(out["notes"], 2)
        self.assertEqual(out["waveforms"], [1, 0, 0, 0, 0, 0, 0, 0])
        self.assertEqual(out["custom_notes"], 1)
        self.assertEqual(out["effects"], [0, 1, 0, 0, 0, 0, 0, 1])
        self.assertEqual(out["pitch_range"], [5, 10])
        self.assertEqual(out["looping_sfx"], 1)

    def test_empty_sfx_has_null_pitch_range(self):
        out = parse(cart_png(bytes(0x8020)))
        self.assertEqual(out["sfx_used"], 0)
        self.assertEqual(out["notes"], 0)
        self.assertIsNone(out["pitch_range"])

    def test_music_patterns_and_flags(self):
        mem = bytearray(0x8020)
        # Unused patterns hold 41 42 43 44: channel disabled, sfx number kept.
        mem[0x3100:0x3200] = bytes([0x41, 0x42, 0x43, 0x44]) * 64
        # Pattern 0: sfx 2 on channels 0 and 3; loop start, loop end and stop
        # ride on channels 0, 1 and 2 together with the disabled flag.
        mem[0x3100:0x3104] = bytes([0x02 | 0x80, 0x05 | 0x40 | 0x80, 0x03 | 0x40 | 0x80, 0x3F])
        mem[0x3104:0x3108] = bytes([0x40, 0x40, 0x40, 0x40])  # all channels disabled
        out = parse(cart_png(mem))
        self.assertEqual(out["patterns"], 1)
        self.assertEqual(out["music_sfx"], [2, 63])
        self.assertEqual(out["loop_starts"], 1)
        self.assertEqual(out["loop_ends"], 1)
        self.assertEqual(out["stops"], 1)
