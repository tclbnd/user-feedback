"""Regression tests for MIDI Sample Dump Standard parsing.

The helper builder writes .syx transfers using the same layout as real
hardware-sampler dumps: LSB-first multi-byte header fields, data packets of
120 seven-bit bytes with an XOR checksum, and sample words packed into
LSB-first seven-bit groups with sign extension.
"""
import unittest

from sdskit.sds import parse, SdsError

DATA_BYTES = 120


def encode_num(value, nbytes):
    return bytes((value >> (7 * i)) & 0x7F for i in range(nbytes))


def make_header(device, sample_number, bits, period, length,
                loop_start=0, loop_end=0, loop_type=0x7F):
    return (bytes([0xF0, 0x7E, device, 0x01]) + encode_num(sample_number, 2) +
            bytes([bits]) + encode_num(period, 3) + encode_num(length, 3) +
            encode_num(loop_start, 3) + encode_num(loop_end, 3) +
            bytes([loop_type, 0xF7]))


def pack_words(words, bits):
    groups_per_word = -(-bits // 7)
    mask = (1 << bits) - 1
    out = bytearray()
    for word in words:
        word &= mask
        for j in range(groups_per_word):
            out.append((word >> (7 * j)) & 0x7F)
    return bytes(out)


def make_packet(device, number, body, corrupt=False):
    preamble = bytes([0x7E, device, 0x02, number])
    checksum = 0
    for b in preamble + body:
        checksum ^= b
    checksum &= 0x7F
    if corrupt:
        checksum ^= 0x01
    return bytes([0xF0]) + preamble + body + bytes([checksum, 0xF7])


def make_handshake(device, sub_id, number=0):
    return bytes([0xF0, 0x7E, device, sub_id, number, 0xF7])


def build_transfer(device, words, bits, period, sample_number, *,
                   loop_type=0x7F, loop_start=0, loop_end=0,
                   start_number=0, corrupt=(), order=None):
    packed = pack_words(words, bits)
    groups_per_word = -(-bits // 7)
    packet_count = -(-len(packed) // DATA_BYTES)
    bodies = []
    for i in range(packet_count):
        body = packed[i * DATA_BYTES:(i + 1) * DATA_BYTES]
        bodies.append(body + bytes(DATA_BYTES - len(body)))
    if order is None:
        order = range(packet_count)
    messages = [make_header(device, sample_number, bits, period, len(words),
                            loop_start, loop_end, loop_type)]
    for sequence, index in enumerate(order):
        number = (start_number + index) & 0x7F
        messages.append(make_packet(device, number, bodies[index], index in corrupt))
    messages.append(make_handshake(device, 0x7B, (start_number + packet_count) & 0x7F))
    return b"".join(messages)


class HeaderTests(unittest.TestCase):
    def test_multi_byte_fields_are_lsb_first(self):
        words = [1, 2, 3, 4]
        data = build_transfer(3, words, 16, 20833, 130,
                              loop_type=0x00, loop_start=400, loop_end=2950)
        out = parse(data)
        self.assertEqual(out["sample_number"], 130)
        self.assertEqual(out["length"], 4)
        self.assertEqual(out["loop"], {"start": 400, "end": 2950, "type": "forward"})

    def test_sample_rate_from_period_and_duration(self):
        data = build_transfer(0, [0, 1], 16, 31250, 7)
        out = parse(data)
        self.assertEqual(out["sample_rate"], "32000")
        self.assertEqual(out["duration"], "1/16000")

    def test_large_period_is_fraction(self):
        data = build_transfer(0, [0, 1], 16, 20833, 1)
        out = parse(data)
        self.assertEqual(out["sample_rate"], "1000000000/20833")

    def test_zero_period_gives_nulls(self):
        data = build_transfer(0, [0, 1], 16, 0, 1)
        out = parse(data)
        self.assertIsNone(out["sample_rate"])
        self.assertIsNone(out["duration"])


class ChecksumTests(unittest.TestCase):
    def test_good_packets_have_no_bad_checksums(self):
        data = build_transfer(2, list(range(100)), 8, 10000, 1)
        out = parse(data)
        self.assertEqual(out["bad_checksums"], 0)
        self.assertEqual(out["packets"], 2)

    def test_corrupt_checksum_is_flagged(self):
        data = build_transfer(2, list(range(100)), 8, 10000, 1, corrupt=(1,))
        out = parse(data)
        self.assertEqual(out["bad_checksums"], 1)


class SampleTests(unittest.TestCase):
    def test_sixteen_bit_signed_words_round_trip(self):
        words = [0, 1, -1, 32767, -32768, 16384, -16384, 256, -257]
        out = parse(build_transfer(5, words, 16, 10000, 9))
        self.assertEqual(out["samples"], len(words))
        self.assertTrue(out["complete"])
        self.assertEqual(out["first_sample"], 0)
        self.assertEqual(out["peak"], "1.000000")

    def test_eight_bit_signed_words_round_trip(self):
        words = [127, -128, 0, -1, 1, 64, -64]
        out = parse(build_transfer(5, words, 8, 10000, 9))
        self.assertEqual(out["peak"], "1.000000")
        self.assertEqual(out["first_sample"], 127)

    def test_twelve_bit_signed_words_round_trip(self):
        words = [0, 1, -1, 2047, -2048, 1000, -1000]
        out = parse(build_transfer(5, words, 12, 10000, 9))
        self.assertEqual(out["samples"], len(words))
        self.assertEqual(out["peak"], "1.000000")

    def test_silent_sample_has_zero_peak(self):
        out = parse(build_transfer(1, [0] * 50, 16, 25000, 7))
        self.assertEqual(out["peak"], "0.000000")
        self.assertEqual(out["first_sample"], 0)

    def test_peak_is_fraction_of_full_scale(self):
        words = [0, 16384]  # half of 16-bit full scale
        out = parse(build_transfer(1, words, 16, 25000, 7))
        self.assertEqual(out["peak"], "0.500000")

    def test_packing_carries_across_packet_boundaries(self):
        # 16-bit words use three seven-bit groups each; 40 words = 120 groups,
        # so the 41st word starts inside the second packet.
        words = [((i * 997) % 2001) - 1000 for i in range(85)]
        out = parse(build_transfer(4, words, 16, 10000, 3))
        self.assertEqual(out["samples"], 85)
        self.assertTrue(out["complete"])
        self.assertEqual(out["first_sample"], words[0])


class LoopTests(unittest.TestCase):
    def test_loop_off_is_null_even_with_points(self):
        data = build_transfer(3, [1, 2, 3], 16, 10000, 1,
                              loop_type=0x7F, loop_start=5, loop_end=6)
        self.assertIsNone(parse(data)["loop"])

    def test_alternating_loop(self):
        data = build_transfer(3, [1, 2, 3], 16, 10000, 1,
                              loop_type=0x01, loop_start=0, loop_end=2)
        self.assertEqual(parse(data)["loop"],
                         {"start": 0, "end": 2, "type": "alternating"})

    def test_unknown_loop_type_is_preserved(self):
        data = build_transfer(3, [1, 2, 3], 16, 10000, 1,
                              loop_type=0x05, loop_start=0, loop_end=2)
        self.assertEqual(parse(data)["loop"]["type"], "type_5")


class PacketOrderTests(unittest.TestCase):
    def test_transfer_need_not_start_at_packet_zero(self):
        data = build_transfer(4, list(range(60)), 16, 10000, 3, start_number=110)
        out = parse(data)
        self.assertEqual(out["out_of_order"], 0)
        self.assertEqual(out["first_sample"], 0)

    def test_out_of_order_numbers_are_counted(self):
        # four packets, emitted with running numbers 0, 2, 1, 3
        data = build_transfer(6, list(range(200)), 8, 10000, 3,
                              order=[0, 2, 1, 3])
        self.assertEqual(parse(data)["out_of_order"], 3)

    def test_wrapping_packet_numbers_stay_in_order(self):
        data = build_transfer(4, list(range(120)), 16, 10000, 3, start_number=126)
        self.assertEqual(parse(data)["out_of_order"], 0)


class TransferShapeTests(unittest.TestCase):
    def test_no_header_raises(self):
        with self.assertRaises(SdsError):
            parse(bytes([0xF0, 0x41, 0x10, 0x00, 0xF7]))

    def test_header_only(self):
        out = parse(make_header(0, 0, 8, 10000, 100))
        self.assertEqual(out["packets"], 0)
        self.assertEqual(out["samples"], 0)
        self.assertFalse(out["complete"])
        self.assertIsNone(out["first_sample"])
        self.assertIsNone(out["peak"])
        self.assertIsNone(out["loop"])

    def test_non_sds_sysex_is_ignored(self):
        data = bytearray(build_transfer(8, [10, 20], 16, 10000, 2))
        data[148:148] = bytes([0xF0, 0x41, 0x10, 0x00, 0x77, 0x12, 0xF7])
        out = parse(bytes(data))
        self.assertEqual(out["packets"], 1)
        self.assertEqual(out["samples"], 2)
        self.assertEqual(out["handshakes"], {"eof": 1})

    def test_all_handshakes_are_counted_by_name(self):
        data = build_transfer(8, [10], 16, 10000, 2)
        for sub_id in (0x7F, 0x7E, 0x7D, 0x7C):
            data += make_handshake(8, sub_id, 1)
        out = parse(data)
        self.assertEqual(out["handshakes"],
                         {"ack": 1, "nak": 1, "cancel": 1, "wait": 1, "eof": 1})

    def test_truncated_transfer_is_incomplete(self):
        words = list(range(300))  # five full 8-bit packets
        full = build_transfer(7, words, 8, 10000, 3)
        truncated = full[:-127]  # drop the last packet plus its trailing data
        out = parse(truncated)
        self.assertFalse(out["complete"])
        self.assertEqual(out["samples"], 240)
        self.assertEqual(parse(full)["samples"], 300)


if __name__ == "__main__":
    unittest.main()
