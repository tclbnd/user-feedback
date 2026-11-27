import struct
import unittest

from bubkit.voices import CLOCK, parse, period_of, read_nibble


def snapshot(ld0=0, ld1=0, tg=0, ca=0, cb=0, old0=0, old1=0, prom=None):
    header = b"K005289S" + struct.pack("<HHBBBHH", ld0, ld1, tg, ca, cb, old0, old1)
    return header + bytes(512 if prom is None else prom)


def write_nibble(buf, channel, waveform, pos, value):
    addr = (channel & 1) << 8 | (waveform & 7) << 5 | (pos & 0x1F)
    index = 19 + (addr >> 1)
    if addr & 1:
        buf[index] = (buf[index] & 0x0F) | ((value & 0x0F) << 4)
    else:
        buf[index] = (buf[index] & 0xF0) | (value & 0x0F)


class PitchTests(unittest.TestCase):
    def test_pitch_is_inverted_down_counter(self):
        # latch 0x100 -> period 3840; latch 0xF00 -> period 256 (higher note)
        self.assertEqual(period_of(0x100), 3840)
        self.assertEqual(period_of(0xF00), 256)
        self.assertEqual(period_of(0xFFF), 1)
        self.assertEqual(period_of(0x000), 4096)

    def test_triggered_latch_takes_effect(self):
        out = parse(snapshot(ld0=0xE40, tg=0x01, old0=0x123))
        ch = out["channels"][0]
        self.assertEqual(ch["frequency"], 0xE40)
        self.assertIsNone(ch["pending"])
        self.assertEqual(ch["cycle_hz"], "%.3f" % (CLOCK / (32 * period_of(0xE40))))

    def test_untriggered_latch_stays_pending(self):
        out = parse(snapshot(ld0=0xEE0, tg=0x00, old0=0x150))
        ch = out["channels"][0]
        self.assertEqual(ch["frequency"], 0x150)
        self.assertEqual(ch["pending"], 0xEE0)

    def test_channel_two_trigger_independent(self):
        out = parse(snapshot(ld0=0x100, ld1=0x200, tg=0x02, old0=0x111, old1=0x222))
        c1, c2 = out["channels"]
        self.assertEqual(c1["frequency"], 0x111)
        self.assertEqual(c1["pending"], 0x100)
        self.assertEqual(c2["frequency"], 0x200)
        self.assertIsNone(c2["pending"])

    def test_extreme_latches_do_not_divide_by_zero(self):
        # the raw latch value 0 and 0xFFF used to crash the renderer; now the
        # period is 4096 and 1 respectively and cycle_hz is always computable
        self.assertEqual(period_of(0), 4096)
        out = parse(snapshot(ld0=0xFFF, tg=0x01))
        self.assertEqual(out["channels"][0]["cycle_hz"],
                         "%.3f" % (CLOCK / 32))
        out = parse(snapshot(ld0=0x000, tg=0x01))
        self.assertEqual(out["channels"][0]["cycle_hz"],
                         "%.3f" % (CLOCK / (32 * 4096)))


class ControlPortTests(unittest.TestCase):
    def test_volume_low_nibble_waveform_high_bits(self):
        out = parse(snapshot(ca=0x4C, cb=0x88, tg=0x03))
        c1, c2 = out["channels"]
        self.assertEqual((c1["volume"], c1["waveform"]), (12, 4))
        self.assertEqual((c2["volume"], c2["waveform"]), (8, 0))


class PromTests(unittest.TestCase):
    def test_nibble_addressing(self):
        buf = bytearray(snapshot())
        write_nibble(buf, 0, 0, 0, 0x0)
        write_nibble(buf, 0, 0, 1, 0xF)
        write_nibble(buf, 1, 7, 31, 0x9)
        prom = bytes(buf[19:])
        self.assertEqual(read_nibble(prom, 0, 0, 0), 0x0)
        self.assertEqual(read_nibble(prom, 0, 0, 1), 0xF)
        self.assertEqual(read_nibble(prom, 1, 7, 31), 0x9)

    def test_channel_two_has_its_own_waveform(self):
        buf = bytearray(snapshot())
        write_nibble(buf, 0, 0, 0, 0x0)   # signed -8 in channel 1
        write_nibble(buf, 1, 0, 0, 0xF)   # signed +7 in channel 2
        out = parse(bytes(buf))
        c1, c2 = out["channels"]
        self.assertEqual(c1["prom_address"], 0)
        self.assertEqual(c2["prom_address"], 256)
        self.assertEqual(c1["wave_min"], -8)
        self.assertEqual(c2["wave_max"], 7)

    def test_wave_stats_use_signed_nibbles(self):
        buf = bytearray(snapshot(tg=0x01, ca=0x00))
        for pos in range(32):
            write_nibble(buf, 0, 0, pos, 0x0 if pos % 2 else 0xF)
        out = parse(bytes(buf))
        ch = out["channels"][0]
        self.assertEqual(ch["wave_min"], -8)
        self.assertEqual(ch["wave_max"], 7)
        self.assertEqual(ch["wave_sum"], 16 * -8 + 16 * 7)

    def test_incomplete_prom(self):
        out = parse(snapshot(prom=b""))
        self.assertFalse(out["prom_complete"])
        for ch in out["channels"]:
            self.assertIsNone(ch["wave_min"])
            self.assertIsNone(ch["wave_max"])
            self.assertIsNone(ch["wave_sum"])
        # missing bytes read as zero -> signed sample -8
        self.assertEqual(out["output"]["samples"], 65536)

    def test_truncated_prom_has_no_wave_stats(self):
        # nibbles interleave the two channel banks, so any short PROM cannot
        # hold a complete 32-nibble waveform; stats stay null but mixing works
        out = parse(snapshot(prom=bytes(256)))
        self.assertFalse(out["prom_complete"])
        for ch in out["channels"]:
            self.assertIsNone(ch["wave_min"])
            self.assertIsNone(ch["wave_max"])
            self.assertIsNone(ch["wave_sum"])
        self.assertEqual(out["output"]["samples"], 65536)


class MixTests(unittest.TestCase):
    def test_mix_rhythm_and_levels(self):
        buf = bytearray(snapshot(ld0=0xFFF, ld1=0xFFF, tg=0x03, ca=0x11, cb=0x12))
        write_nibble(buf, 0, 1, 0, 0xF)  # +7, volume 1, waveform 1
        write_nibble(buf, 1, 1, 0, 0x8)  # 0, volume 2, waveform 1
        out = parse(bytes(buf))
        samples = out["output"]
        # period 1 -> position advances every clock, so output must change
        self.assertEqual(samples["rate_hz"], CLOCK)
        self.assertEqual(samples["samples"], 65536)
        self.assertGreater(samples["peak"], 0)
        self.assertEqual(len(samples["crc32"]), 8)

    def test_static_output_from_flat_waveform(self):
        # every step of channel 1's waveform is +7, so any counter position
        # yields the same sample; channel 2 is muted
        buf = bytearray(snapshot(ld0=0x000, tg=0x03, ca=0x01))
        for pos in range(32):
            write_nibble(buf, 0, 0, pos, 0xF)
        write_nibble(buf, 1, 7, 0, 0x0)
        out = parse(bytes(buf))
        self.assertEqual(out["output"]["peak"], 7)
        self.assertEqual(out["output"]["sum"], 7 * 65536)


if __name__ == "__main__":
    unittest.main()
