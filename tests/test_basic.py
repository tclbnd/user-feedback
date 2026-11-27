import unittest

from dpcmkit.dpcm import Decoder, DpcmError, parse


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


class InterplayTests(unittest.TestCase):
    def test_seed_is_first_sample(self):
        # The predictor seed carried by the packet is output, then the deltas.
        seed = (32767).to_bytes(2, "little", signed=True)
        out = parse(pak(b"IPLY", 1, [b"\0" * 6 + seed + bytes([1])]))
        self.assertEqual(out["packets"], [2])
        self.assertEqual(out["frames"], 2)

    def test_overflow_clips_instead_of_wrapping(self):
        d = Decoder("interplay", 1)
        seed = (32767).to_bytes(2, "little", signed=True)
        # Table index 1 is +1: clipping stays at the rail, wrapping would jump negative.
        res = d.packet(b"\0" * 6 + seed + bytes([1]))
        self.assertEqual(res, [32767, 32767])

    def test_stereo_odd_delta_count(self):
        d = Decoder("interplay", 2)
        body = b"\0" * 6 + (100).to_bytes(2, "little", signed=True) \
            + (200).to_bytes(2, "little", signed=True) + b"\x01\x01\x01"
        # Two seeds plus three alternating deltas (the last one only feeds channel 0).
        self.assertEqual(d.packet(body), [100, 200, 101, 201, 102])


class XanTests(unittest.TestCase):
    def test_shift_is_clamped_to_ten(self):
        d = Decoder("xan", 1)
        d.packet((0).to_bytes(2, "little") + bytes([0x01] * 40))
        self.assertEqual(d.shift[0], 10)

    def test_shift_persists_across_packets(self):
        d = Decoder("xan", 1)
        d.packet((0).to_bytes(2, "little") + bytes([0x01] * 10))
        self.assertEqual(d.shift[0], 10)
        # A fresh decoder would restart at shift 4 and grow again; the continuing
        # decoder must start the next packet at 10 and only drop by one.
        d.packet((0).to_bytes(2, "little") + bytes([0x03]))
        self.assertEqual(d.shift[0], 9)

    def test_stereo_frame_count(self):
        d = Decoder("xan", 2)
        body = (0).to_bytes(2, "little") * 2 + bytes([0] * 8)
        self.assertEqual(len(d.packet(body)), 8)


class Sol8Tests(unittest.TestCase):
    def test_high_nibble_is_channel_zero(self):
        d = Decoder("sol-new", 2)
        # SOL_NEW[1] == 1, SOL_NEW[2] == 2: high nibble first.
        self.assertEqual(d.packet(bytes([0x12])), [1, 2])

    def test_predictor_persists_across_packets(self):
        out = parse(pak(b"SOL2", 2, [bytes([0x12]), bytes([0x00])]))
        ch0, ch1 = out["channel"]
        # Both packets contribute one frame per channel; the second packet adds no delta,
        # so channel 0 holds 1,1 (sum around 128*2 below zero) and channel 1 holds 2,2.
        self.assertEqual(ch0["samples"], 2)
        self.assertEqual(ch1["samples"], 2)
        self.assertEqual(ch0["sum"], (1 - 128) * 2)
        self.assertEqual(ch1["sum"], (2 - 128) * 2)

    def test_old_table_negative_nibbles(self):
        d = Decoder("sol-old", 1)
        # High nibble of 0x80 is index 8 (-21 in SOL_OLD); mono writes both nibbles
        # to channel 0, and the predictor clips at the unsigned 8-bit rail.
        self.assertEqual(d.packet(bytes([0x80])), [0, 0])


class Sol16Tests(unittest.TestCase):
    def test_negative_step_uses_16384_bias(self):
        d = Decoder("sol-16", 1)
        # +8, then 8 - 16384 = -16376 -> predictor -16368.
        self.assertEqual(d.packet(bytes([0x01, 0x81])), [8, -16368])

    def test_predictor_wraps_like_int16(self):
        d = Decoder("sol-16", 1)
        d.sample[0] = 32767
        self.assertEqual(d.packet(bytes([0x01])), [-32761])

    def test_state_persists_across_packets(self):
        out = parse(pak(b"SOL3", 1, [bytes([0x01]), bytes([0x01])]))
        # Second packet continues from 8 rather than restarting at zero.
        self.assertEqual(out["channel"][0]["sum"], 8 + 16)


if __name__ == "__main__":
    unittest.main()
