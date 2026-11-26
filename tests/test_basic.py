import struct
import unittest

from pokeykit.regs import CLOCK, DumpError, parse


def one_frame(ctl, audf, audc, system="NTSC"):
    data = b"POKEYREG" + bytes([1 if system == "PAL" else 0, 0]) + struct.pack("<I", 1)
    data += bytes([audf[0], audc[0], audf[1], audc[1],
                   audf[2], audc[2], audf[3], audc[3], ctl])
    return parse(data)


class BasicTests(unittest.TestCase):
    def test_not_dump(self):
        with self.assertRaises(DumpError):
            parse(b"SAP\r\n" + bytes(20))

    def test_silent_frames(self):
        data = b"POKEYREG" + bytes(2) + struct.pack("<I", 3) + bytes(27)
        out = parse(data)
        self.assertEqual(out["frames"], 3)
        self.assertEqual(out["channels"][0]["sounding_frames"], 0)

    def test_truncated_dump(self):
        data = b"POKEYREG" + bytes(2) + struct.pack("<I", 5) + bytes(9 * 2)
        out = parse(data)
        self.assertEqual(out["frames"], 2)
        self.assertFalse(out["complete"])

    def test_64k_default_clock(self):
        out = one_frame(0x00, [9, 0, 0, 0], [0xAF, 0, 0, 0])
        expected = CLOCK["NTSC"] / 28 / (2 * 10)
        self.assertEqual(out["channels"][0]["first_freq"], "%.3f" % expected)

    def test_15khz_clock_no_divide_by_two(self):
        # AUDCTL bit 0: timers 1 & 2 run from master/114, no extra /2.
        out = one_frame(0x01, [121, 0, 0, 0], [0xAF, 0, 0, 0])
        expected = CLOCK["NTSC"] / 114 / 122
        self.assertEqual(out["channels"][0]["first_freq"], "%.3f" % expected)
        self.assertEqual(out["channels"][1]["first_freq"], None)

    def test_high_clock_bits_identify_right_channels(self):
        # Bit 4 (0x10): high clock for timers 3 & 4 only.
        out = one_frame(0x10, [9, 0, 9, 0], [0xAF, 0, 0xAF, 0])
        self.assertEqual(out["channels"][0]["first_freq"],
                         "%.3f" % (CLOCK["NTSC"] / 28 / 20))
        self.assertEqual(out["channels"][2]["first_freq"],
                         "%.3f" % (CLOCK["NTSC"] / 10))
        # Bit 5 (0x20): high clock for timers 1 & 2 only.
        out = one_frame(0x20, [9, 0, 9, 0], [0xAF, 0, 0xAF, 0])
        self.assertEqual(out["channels"][0]["first_freq"],
                         "%.3f" % (CLOCK["NTSC"] / 10))
        self.assertEqual(out["channels"][2]["first_freq"],
                         "%.3f" % (CLOCK["NTSC"] / 28 / 20))

    def test_joined_16_bit_pair(self):
        # Bit 6 joins timers 1 & 2: divider = AUDF1 + AUDF2*256 + 1, 64 kHz path.
        out = one_frame(0x40, [7, 0xAC, 0, 0], [0, 0xAF, 0, 0])
        divider = 7 + 0xAC * 256 + 1
        expected = CLOCK["NTSC"] / 28 / (2 * divider)
        self.assertEqual(out["channels"][1]["first_freq"], "%.3f" % expected)

    def test_joined_pair_with_15khz_and_high_clock(self):
        out = one_frame(0x41, [100, 1, 0, 0], [0, 0xAF, 0, 0])
        divider = 100 + 256 + 1
        self.assertEqual(out["channels"][1]["first_freq"],
                         "%.3f" % (CLOCK["NTSC"] / 114 / divider))
        # Bit 7 joins 3 & 4; bit 4 selects the 1.79 MHz clock for the pair.
        out = one_frame(0x90, [0, 0, 0, 1], [0, 0, 0, 0xAF])
        divider = 257
        self.assertEqual(out["channels"][3]["first_freq"],
                         "%.3f" % (CLOCK["NTSC"] / divider))

    def test_frame_rates_and_duration(self):
        ntsc = one_frame(0, [0] * 4, [0] * 4)
        self.assertEqual(ntsc["frame_rate"], "59.923")
        self.assertEqual(ntsc["duration"], "0.017")
        pal = one_frame(0, [0] * 4, [0] * 4, system="PAL")
        self.assertEqual(pal["frame_rate"], "49.861")
        self.assertEqual(pal["duration"], "0.020")

    def test_distortion_e_is_pure_tone(self):
        out = one_frame(0, [5, 5, 5, 5], [0xEA, 0xAA, 0xDA, 0x8A])
        pure = [c["pure_frames"] for c in out["channels"]]
        self.assertEqual(pure, [1, 1, 0, 0])
        # Bit 4 of the distortion nibble still drives volume-only counting.
        self.assertEqual(out["channels"][2]["volume_only_frames"], 1)
        self.assertEqual(out["channels"][0]["volume_only_frames"], 0)

    def test_high_pass_filter_mapping(self):
        # Bits 1, 2, 3 high-pass channels 2, 3, 4 respectively.
        out = one_frame(0x0E, [0, 0, 0, 0], [0, 0xAF, 0xAF, 0xAF])
        self.assertEqual([c["filtered_frames"] for c in out["channels"]],
                         [0, 1, 1, 1])


if __name__ == "__main__":
    unittest.main()
