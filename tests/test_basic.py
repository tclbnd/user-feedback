import unittest

from dmckit.dmc import DmcError, parse, NTSC_RATE, PAL_RATE, CPU

HEADER = b"NESDMC"


def snapshot(region=0, r0=0, r1=0, r2=0, r3=0, prg=None):
    if prg is None:
        prg = bytes(0x8000)
    return HEADER + bytes([region, r0, r1, r2, r3]) + prg


class BasicTests(unittest.TestCase):
    def test_flags(self):
        data = b"NESDMC\x00" + bytes([0xCF, 0, 0, 0]) + bytes(0x8000)
        out = parse(data)
        self.assertTrue(out["loop"])
        self.assertTrue(out["irq"])

    def test_not_a_snapshot(self):
        with self.assertRaises(DmcError):
            parse(b"NESXXX" + bytes(20))

    def test_address_and_length_formulas(self):
        # $4012 -> $C000 + index*64 ; $4013 -> index*64 + 1 (never zero bytes)
        out = parse(snapshot(r2=0, r3=0))
        self.assertEqual(out["address"], "$C000")
        self.assertEqual(out["length"], 1)
        out = parse(snapshot(r2=255, r3=255))
        self.assertEqual(out["address"], "$FFC0")
        self.assertEqual(out["length"], 255 * 64 + 1)  # ends exactly at $FFFF

    def test_region_rate_tables(self):
        ntsc = parse(snapshot(region=0, r0=14))
        pal = parse(snapshot(region=1, r0=14))
        self.assertEqual(ntsc["period"], NTSC_RATE[14])  # 72 NTSC cycles
        self.assertEqual(pal["period"], PAL_RATE[14])    # 66 PAL cycles
        self.assertEqual(ntsc["rate_hz"], "%.2f" % (CPU[0] / NTSC_RATE[14]))
        self.assertEqual(pal["rate_hz"], "%.2f" % (CPU[1] / PAL_RATE[14]))

    def test_duration_uses_full_length_and_region_clock(self):
        out = parse(snapshot(region=1, r0=0, r3=0))  # 1 byte = 8 bits, PAL
        self.assertEqual(out["duration"], "%.6f" % (8 * PAL_RATE[0] / CPU[1]))

    def test_bits_are_consumed_lsb_first(self):
        # 0x01 at $C000, starting level 0: bit 0 raises to 2, bit 1 lowers to 0, rest silent
        prg = bytearray(0x8000)
        prg[0x4000] = 0x01
        out = parse(snapshot(r1=0, r3=0, prg=bytes(prg)))
        self.assertEqual(out["final_level"], 0)
        self.assertEqual(out["max_level"], 2)
        self.assertEqual(out["level_changes"], 2)

    def test_level_is_clamped_to_seven_bit_range(self):
        prg = bytearray(0x8000)
        prg[0x4000] = 0xFF  # eight raise requests
        out = parse(snapshot(r1=123, r3=0, prg=bytes(prg)))  # 123 -> 125 -> 127, then silent
        self.assertEqual(out["final_level"], 127)
        self.assertEqual(out["max_level"], 127)
        self.assertEqual(out["level_changes"], 2)
        prg[0x4000] = 0x00  # eight lower requests
        out = parse(snapshot(r1=2, r3=0, prg=bytes(prg)))  # 2 -> 0, then silent
        self.assertEqual(out["final_level"], 0)
        self.assertEqual(out["min_level"], 0)
        self.assertEqual(out["level_changes"], 1)

    def test_initial_level_masks_high_bit(self):
        prg = bytearray(0x8000)
        prg[0x4000] = 0xFF
        out = parse(snapshot(r1=0xFF, r3=0, prg=bytes(prg)))
        self.assertEqual(out["initial_level"], 127)

    def test_address_counter_wraps_inside_prg_window(self):
        prg = bytearray(0x8000)
        prg[0x7F00] = 0xFF  # byte at $FF00
        out = parse(snapshot(r2=252, r3=10, prg=bytes(prg)))  # start $FF00, runs past $FFFF
        self.assertEqual(out["address"], "$FF00")
        self.assertTrue(out["complete"])
        self.assertGreater(out["max_level"], 0)

    def test_truncated_prg_is_incomplete(self):
        out = parse(snapshot(r3=5, prg=bytes(100)))  # nominal 321 bytes, only 100 present
        self.assertFalse(out["complete"])
        self.assertEqual(out["length"], 321)
