import unittest

from sdskit.sds import parse

HEADER = bytes([0xF0, 0x7E, 0x00, 0x01, 0x00, 0x00, 8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x7F, 0xF7])


def groups(value, count):
    """SDS multi-byte number: least significant 7-bit group first."""
    return bytes([(value >> (7 * i)) & 0x7F for i in range(count)])


def header(sample_number=5, bits=16, period=31250, length=10,
           loop_start=0, loop_end=0, loop_type=0x7F, device=0):
    return (bytes([0xF0, 0x7E, device, 0x01]) + groups(sample_number, 2)
            + bytes([bits]) + groups(period, 3) + groups(length, 3)
            + groups(loop_start, 3) + groups(loop_end, 3)
            + bytes([loop_type, 0xF7]))


def packet(number, body, device=0, corrupt=False):
    body = (bytes(body) + bytes(120))[:120]
    checksum = 0
    for b in bytes([0x7E, device, 0x02, number]) + body:
        checksum ^= b
    if corrupt:
        checksum ^= 0x01
    return bytes([0xF0, 0x7E, device, 0x02, number]) + body + bytes([checksum & 0x7F, 0xF7])


def pack_words(words, bits):
    per = (bits + 6) // 7
    shift = per * 7 - bits
    span = 1 << bits
    out = bytearray()
    for word in words:
        word &= span - 1
        field = word << shift
        for j in range(per):
            out.append((field >> (7 * (per - 1 - j))) & 0x7F)
    return bytes(out)


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(HEADER)
        self.assertEqual((out["device_id"], out["bits"], out["packets"]), (0, 8, 0))

    def test_no_samples(self):
        out = parse(HEADER)
        self.assertEqual((out["samples"], out["first_sample"]), (0, None))


class SdsStandardTests(unittest.TestCase):
    def test_header_numbers_are_lsb_first(self):
        from fractions import Fraction
        out = parse(header(sample_number=0x203, period=0x1A2B3C, length=0x12345))
        self.assertEqual(out["sample_number"], 0x203)
        self.assertEqual(out["sample_rate"], str(Fraction(10 ** 9, 0x1A2B3C)))
        self.assertEqual(out["length"], 0x12345)

    def test_checksum_covers_7e_through_data(self):
        data = header(length=40) + packet(0, bytes(120)) + packet(1, bytes(120), corrupt=True)
        out = parse(data)
        self.assertEqual(out["packets"], 2)
        self.assertEqual(out["bad_checksums"], 1)
        self.assertEqual(out["out_of_order"], 0)

    def test_sixteen_bit_words_are_signed_two_complement(self):
        words = [0, -1, 1, -32768, 32767, 256, -257]
        data = header(bits=16, length=len(words)) + packet(0, pack_words(words, 16))
        out = parse(data)
        self.assertEqual(out["samples"], len(words))
        self.assertTrue(out["complete"])
        self.assertEqual(out["first_sample"], 0)
        # re-decode the same body to inspect every word
        from sdskit.sds import decode_words
        self.assertEqual(decode_words(pack_words(words, 16), 16)[:len(words)], words)
        self.assertEqual(out["peak"], "%.6f" % (32768 / 32768))

    def test_eight_bit_signed_words(self):
        words = [127, -128, -1, 0]
        data = header(bits=8, length=len(words)) + packet(0, pack_words(words, 8))
        out = parse(data)
        self.assertEqual(out["first_sample"], 127)
        self.assertEqual(out["peak"], "%.6f" % (128 / 128))
        from sdskit.sds import decode_words
        self.assertEqual(decode_words(pack_words(words, 8), 8)[:len(words)], words)

    def test_silent_sample_has_zero_peak(self):
        data = header(bits=16, length=40) + packet(0, bytes(120))
        out = parse(data)
        self.assertEqual(out["samples"], 40)
        self.assertEqual(out["first_sample"], 0)
        self.assertEqual(out["peak"], "0.000000")

    def test_loop_off_emits_null(self):
        out = parse(header(loop_type=0x7F, loop_start=10, loop_end=20))
        self.assertIsNone(out["loop"])

    def test_loop_on_keeps_points(self):
        out = parse(header(loop_type=0x01, loop_start=11, loop_end=22, length=1))
        self.assertEqual(out["loop"], {"start": 11, "end": 22, "type": "alternating"})

    def test_out_of_order_packet_numbering(self):
        data = header(length=80) + packet(0, bytes(120)) + packet(3, bytes(120))
        out = parse(data)
        self.assertEqual(out["out_of_order"], 1)
        self.assertEqual(out["bad_checksums"], 0)


if __name__ == "__main__":
    unittest.main()

