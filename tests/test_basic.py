import unittest

from kdsckit.voices import CLOCK, KADPCM, KdscError, parse


def build_regs(voices, bank=0):
    """voices: up to 4 tuples (pitch, length, start, vol, pan, on, rev, adp)."""
    r = bytearray(48)
    r[0x29] = bank
    for i, (pitch, length, start, vol, pan, on, rev, adp) in enumerate(voices):
        b = 8 + 8 * i
        r[b] = pitch & 0xFF
        r[b + 1] = (pitch >> 8) & 0x0F
        r[b + 2] = length & 0xFF
        r[b + 3] = (length >> 8) & 0xFF
        r[b + 4] = start & 0xFF
        r[b + 5] = (start >> 8) & 0xFF
        r[b + 7] = vol
        r[0x2C + i // 2] |= (pan & 7) << (3 * (i & 1))
        if on:
            r[0x28] |= 1 << i
        if rev:
            r[0x28] |= 1 << (i + 4)
        if adp:
            r[0x2A] |= 1 << (i + 4)
    return bytes(r)


BLANK = (0, 0, 0, 0, 0, False, False, False)


class BasicTests(unittest.TestCase):
    def test_no_voice_keyed(self):
        out = parse(b"K053260S" + bytes(48) + bytes(256))
        self.assertEqual(out["voices_on"], 0)
        self.assertEqual(out["voices"][0]["format"], "pcm")

    def test_not_a_snapshot(self):
        with self.assertRaises(KdscError):
            parse(b"K053260X" + bytes(64))

    def test_voice_blocks_start_at_register_8(self):
        # Key-on bit 0 must read the block at $08, not an empty block at $00.
        rom = bytes(256)
        cfg = [(1000, 4, 100, 0x40, 3, True, False, False), BLANK, BLANK, BLANK]
        out = parse(b"K053260S" + build_regs(cfg) + rom)
        self.assertEqual(out["voices_on"], 1)
        self.assertTrue(out["voices"][0]["on"])
        self.assertEqual(out["voices"][0]["length"], 4)
        self.assertEqual(out["voices"][1]["on"], False)

    def test_forward_skips_guard_byte_and_includes_last(self):
        rom = bytearray(256)
        rom[100] = 0x77          # guard, must never be output
        rom[101:105] = bytes([10, 20, 200, 5])
        cfg = [(1000, 4, 100, 0x40, 3, True, False, False), BLANK, BLANK, BLANK]
        v = parse(b"K053260S" + build_regs(cfg) + bytes(rom))["voices"][0]
        self.assertEqual(v["samples"], 4)
        self.assertEqual(v["peak"], 56)       # |s8(200)| = 56
        self.assertEqual(v["sum"], 10 + 20 - 56 + 5)
        self.assertEqual(v["last"], 5)
        self.assertTrue(v["complete"])

    def test_reverse_plays_mirrored_span_high_to_low(self):
        rom = bytearray(512)
        start, length = 300, 10
        # Reverse pass offsets are -length-1 .. -2*length: addresses
        # start-length-1 (289) down to start-2*length (280); guard at 290.
        rom[290] = 0
        rom[280] = 7
        rom[289] = 9
        cfg = [(1000, length, start, 0x40, 3, True, True, False), BLANK, BLANK, BLANK]
        v = parse(b"K053260S" + build_regs(cfg) + bytes(rom))["voices"][0]
        self.assertEqual(v["samples"], length)
        self.assertEqual(v["peak"], 9)
        self.assertEqual(v["sum"], sum(rom[280:290]))
        self.assertEqual(v["last"], 7)              # lowest address read last

    def test_kadpcm_step_table_and_nibbles(self):
        self.assertEqual(KADPCM, (1, 2, 4, 8, 16, 32, 64, 128,
                                  -128, -64, -32, -16, -8, -4, -2, -1))
        rom = bytearray(256)
        rom[100] = 0                      # guard
        rom[101] = 0x12                   # nibbles 1 then 2 -> +2, +4
        cfg = [(1000, 1, 100, 0x40, 3, True, False, True), BLANK, BLANK, BLANK]
        v = parse(b"K053260S" + build_regs(cfg) + bytes(rom))["voices"][0]
        self.assertEqual(v["format"], "kadpcm")
        self.assertEqual(v["samples"], 2)
        self.assertEqual(v["sum"], 2 + 6)
        self.assertEqual(v["last"], 6)

    def test_kadpcm_saturates(self):
        rom = bytearray(256)
        rom[101] = 0x77                   # two +128 steps clamp at 127
        cfg = [(1000, 1, 100, 0x40, 3, True, False, True), BLANK, BLANK, BLANK]
        v = parse(b"K053260S" + build_regs(cfg) + bytes(rom))["voices"][0]
        self.assertEqual(v["peak"], 127)
        self.assertEqual(v["last"], 127)

    def test_rate_uses_divide_128(self):
        # pitch is 12 bits; 4095 is full scale and plays at CLOCK/128.
        cfg = [(4095, 1, 100, 0x40, 3, True, False, False), BLANK, BLANK, BLANK]
        v = parse(b"K053260S" + build_regs(cfg) + bytes(256))["voices"][0]
        self.assertEqual(v["rate_hz"], "%.2f" % (CLOCK / 128 * 4095 / 4096))

    def test_pan_weights_divide_by_8(self):
        # pan 0: full left; pan 7: full right; pan 4: balanced halves
        for pan, expect_left, expect_right in ((0, True, False), (7, False, True)):
            cfg = [(1000, 1, 100, 127, pan, True, False, False), BLANK, BLANK, BLANK]
            v = parse(b"K053260S" + build_regs(cfg) + bytes(256))["voices"][0]
            self.assertEqual(v["left"], 127 * 7 * 127 // 8 if expect_left else 0)
            self.assertEqual(v["right"], 127 * 7 * 127 // 8 if expect_right else 0)
        cfg = [(1000, 1, 100, 127, 4, True, False, False), BLANK, BLANK, BLANK]
        v = parse(b"K053260S" + build_regs(cfg) + bytes(256))["voices"][0]
        # pan=4: left weight 3/8, right weight 4/8 (the chip's 3-bit weights)
        self.assertEqual(v["left"], 127 * 3 * 127 // 8)
        self.assertEqual(v["right"], 127 * 4 * 127 // 8)

    def test_bank_register_offsets_high_address(self):
        rom = bytearray(0x12000)
        target = 0x10000 + 101
        rom[target] = 42
        # bank=1, start=100 -> guard at 0x10064, data at 0x10065
        cfg = [(1000, 1, 100, 0x40, 3, True, False, False), BLANK, BLANK, BLANK]
        v = parse(b"K053260S" + build_regs(cfg, bank=1) + bytes(rom))["voices"][0]
        self.assertEqual(v["samples"], 1)
        self.assertEqual(v["last"], 42)
        self.assertTrue(v["complete"])

    def test_incomplete_when_pass_leaves_rom(self):
        rom = bytearray(256)
        cfg = [(1000, 10, 250, 0x40, 3, True, False, False), BLANK, BLANK, BLANK]
        v = parse(b"K053260S" + build_regs(cfg) + rom)["voices"][0]
        self.assertFalse(v["complete"])
        self.assertEqual(v["samples"], 5)       # bytes 251..255 fit

    def test_zero_length_pass_is_empty_but_complete(self):
        cfg = [(1000, 0, 100, 0x40, 3, True, False, False), BLANK, BLANK, BLANK]
        v = parse(b"K053260S" + build_regs(cfg) + bytes(256))["voices"][0]
        self.assertEqual(v["samples"], 0)
        self.assertEqual(v["peak"], 0)
        self.assertIsNone(v["last"])
        self.assertTrue(v["complete"])


if __name__ == "__main__":
    unittest.main()
