import struct
import unittest

from itkit.it import parse

from itbuild import (legacy_sample, modern_sample, module, pack_channel,
                     packed_pattern)

# IMPS flag bits.
SMP_16BIT = 0x04
SMP_STEREO = 0x02
SMP_COMPRESSED = 0x08
SMP_LOOP = 0x10
SMP_SUSTAIN = 0x20
SMP_PINGPONG = 0x40

# Pattern channel mask bits.
M_NOTE = 0x01
M_INSTRUMENT = 0x02
M_VOLPAN = 0x04
M_COMMAND = 0x08


class VersionTests(unittest.TestCase):
    def test_version_format(self):
        out = parse(module(cwt=0x0214, cmwt=0x0200))
        self.assertEqual((out["tracker"], out["compatible"]), ("2.14", "2.00"))

    def test_hex_minor_digit(self):
        out = parse(module(cwt=0x0217, cmwt=0x0214))
        self.assertEqual((out["tracker"], out["compatible"]), ("2.17", "2.14"))


class OrderTests(unittest.TestCase):
    def test_separator_removed_and_song_end_stops_list(self):
        out = parse(module(orders=bytes([0, 1, 254, 2, 255, 3, 0])))
        self.assertEqual(out["orders"], [0, 1, 2])

    def test_no_marker_plays_everything(self):
        out = parse(module(orders=bytes([2, 1, 0])))
        self.assertEqual(out["orders"], [2, 1, 0])

    def test_leading_end_marker(self):
        out = parse(module(orders=bytes([255, 1])))
        self.assertEqual(out["orders"], [])


class ChannelTests(unittest.TestCase):
    def test_disabled_and_surround_pans(self):
        # Bit 7 marks a panned channel, bit 5 marks surround;
        # a zero byte disables the channel.
        pans = [0, 0x40, 0x64, 0x20, 0xa0]
        out = parse(module(pans=pans))
        self.assertEqual(out["channels"], 4)
        self.assertEqual(out["surround_channels"], 3)

    def test_surround_bit_only(self):
        # 0x64: surround (bit 5) with panning position 4; 0x40 is plain pan.
        out = parse(module(pans=[0x64, 0x40]))
        self.assertEqual(out["channels"], 2)
        self.assertEqual(out["surround_channels"], 1)

    def test_all_disabled(self):
        out = parse(module(pans=[0] * 64))
        self.assertEqual((out["channels"], out["surround_channels"]), (0, 0))


class SampleTests(unittest.TestCase):
    def test_modern_flags_and_data_size(self):
        samples = [
            modern_sample(b"plain", length=1000, pointer=10000),
            modern_sample(b"wide", length=500, flags=SMP_16BIT, pointer=20000),
            modern_sample(b"stereo", length=300, flags=SMP_STEREO, pointer=30000),
            modern_sample(b"both", length=200,
                          flags=SMP_16BIT | SMP_STEREO, pointer=40000),
        ]
        out = parse(module(samples=samples))
        got = out["samples"]
        self.assertEqual([s["bits"] for s in got], [8, 16, 8, 16])
        self.assertEqual([s["stereo"] for s in got], [False, False, True, True])
        self.assertEqual([s["data_bytes"] for s in got],
                         [1000, 1000, 600, 800])

    def test_compressed_sample_has_null_data_bytes(self):
        samples = [modern_sample(b"packed", length=900,
                                 flags=SMP_COMPRESSED, pointer=500)]
        out = parse(module(samples=samples))
        sample = out["samples"][0]
        self.assertTrue(sample["compressed"])
        self.assertIsNone(sample["data_bytes"])

    def test_sample_without_data(self):
        out = parse(module(samples=[modern_sample(b"empty", length=1000)]))
        sample = out["samples"][0]
        self.assertFalse(sample["compressed"])
        self.assertIsNone(sample["data_bytes"])

    def test_loops(self):
        samples = [
            modern_sample(b"fwd", flags=SMP_LOOP, loop=(10, 100)),
            modern_sample(b"pp", flags=SMP_LOOP | SMP_PINGPONG,
                          loop=(0, 50)),
            modern_sample(b"sustain", flags=SMP_SUSTAIN,
                          sustain=(4, 8)),
            modern_sample(b"suspp",
                          flags=SMP_SUSTAIN | SMP_PINGPONG),
            modern_sample(b"none"),
        ]
        out = parse(module(samples=samples))
        got = out["samples"]
        self.assertEqual(got[0]["loop"], "forward")
        self.assertEqual(got[1]["loop"], "pingpong")
        self.assertEqual(got[2]["sustain_loop"], "forward")
        self.assertEqual(got[3]["sustain_loop"], "pingpong")
        self.assertIsNone(got[4]["loop"])
        self.assertIsNone(got[4]["sustain_loop"])

    def test_c5_speed_and_name(self):
        out = parse(module(samples=[modern_sample(b"voice", c5=12345)]))
        sample = out["samples"][0]
        self.assertEqual(sample["c5_speed"], 12345)
        self.assertEqual(sample["name"], "voice")

    def test_legacy_header_layout(self):
        samples = [
            legacy_sample(b"old16", length=4000, flags=SMP_16BIT,
                          c5=8363, pointer=1),
            legacy_sample(b"oldloops", flags=SMP_LOOP | SMP_PINGPONG,
                          length=100, loop=(2, 99), pointer=2),
        ]
        out = parse(module(samples=samples))
        got = out["samples"]
        self.assertEqual(got[0]["length"], 4000)
        self.assertEqual(got[0]["bits"], 16)
        self.assertEqual(got[0]["c5_speed"], 8363)
        self.assertEqual(got[0]["data_bytes"], 8000)
        self.assertEqual(got[1]["loop"], "pingpong")


class PatternTests(unittest.TestCase):
    def test_note_off_and_cut_counts(self):
        rows = [
            # Row 0: a note with instrument, volume and command in one channel.
            [pack_channel(M_NOTE | M_INSTRUMENT | M_VOLPAN | M_COMMAND,
                          note=60, instrument=1, volpan=65,
                          command=1, info=5, last=True)],
            # Row 1: note off on one channel, note cut on another.
            [pack_channel(M_NOTE, note=253, last=False),
             pack_channel(M_NOTE, note=254, last=True)],
            # Row 2: highest playable note, then a note fade (255, ignored).
            [pack_channel(M_NOTE, note=119, last=False),
             pack_channel(M_NOTE, note=255, last=True)],
            # Row 3: empty row.
            [],
        ]
        out = parse(module(patterns=[packed_pattern(4, rows)]))
        self.assertEqual(out["pattern_rows"], [4])
        self.assertEqual(out["notes"], 2)
        self.assertEqual(out["note_offs"], 1)
        self.assertEqual(out["note_cuts"], 1)

    def test_totals_accumulate_over_patterns(self):
        rows_a = [[pack_channel(M_NOTE, note=12, last=True)]]
        rows_b = [
            [pack_channel(M_NOTE, note=253, last=True)],
            [pack_channel(M_NOTE, note=254, last=True)],
        ]
        out = parse(module(patterns=[
            packed_pattern(1, rows_a), packed_pattern(2, rows_b)]))
        self.assertEqual((out["notes"], out["note_offs"], out["note_cuts"]),
                         (1, 1, 1))
        self.assertEqual(out["pattern_rows"], [1, 2])

    def test_extended_channel_mask(self):
        # A mask above bit 6 needs the two-byte mask encoding.  The note bit
        # lives in the low byte, the extended bit in the high byte.
        rows = [
            [pack_channel(M_NOTE, note=48, last=True)],
            [pack_channel(0x100 | M_NOTE, note=254, last=True)],
        ]
        out = parse(module(patterns=[packed_pattern(2, rows)]))
        self.assertEqual(out["notes"], 1)
        self.assertEqual(out["note_cuts"], 1)

    def test_extended_mask_high_bits_only(self):
        # A mask using only an extended bit: no note byte on this channel.
        rows = [
            [pack_channel(M_NOTE, note=60, last=True)],
            [pack_channel(0x100, last=True)],
        ]
        out = parse(module(patterns=[packed_pattern(2, rows)]))
        self.assertEqual((out["notes"], out["note_cuts"]), (1, 0))

    def test_pattern_without_magic_marker(self):
        rows = [[pack_channel(M_NOTE, note=253, last=True)]]
        out = parse(module(patterns=[packed_pattern(1, rows, with_magic=False)]))
        self.assertEqual(out["pattern_rows"], [1])
        self.assertEqual(out["note_offs"], 1)

    def test_empty_pattern_slot_is_64_rows(self):
        out = parse(module(patterns=[None]))
        self.assertEqual(out["pattern_rows"], [64])
        self.assertEqual((out["notes"], out["note_offs"], out["note_cuts"]),
                         (0, 0, 0))

    def test_zero_row_header_counts_as_64(self):
        # IMPP, packed length 0, 0 rows, reserved dword.
        pattern = b"IMPP" + struct.pack("<HHI", 0, 0, 0)
        out = parse(module(patterns=[pattern]))
        self.assertEqual(out["pattern_rows"], [64])

    def test_truncated_pattern_raises(self):
        from itkit.it import ItError
        # Header claims four bytes of data but only two are present.
        pattern = b"IMPP" + struct.pack("<HHI", 4, 1, 0) + b"\x81"
        with self.assertRaises(ItError):
            parse(module(patterns=[pattern]))


class MessageTests(unittest.TestCase):
    def test_message_line_breaks(self):
        out = parse(module(message=b"hello\rworld\r\nagain"))
        self.assertEqual(out["message"], "hello\nworld\nagain")

    def test_no_message(self):
        self.assertIsNone(parse(module())["message"])

    def test_empty_message_is_null(self):
        self.assertIsNone(parse(module(message=b""))["message"])


if __name__ == "__main__":
    unittest.main()
