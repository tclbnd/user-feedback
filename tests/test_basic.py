import unittest

from sndkit.snd1 import SndError, chunk, parse


def pack(chunks):
    return b"SND1PACK" + (22050).to_bytes(2, "little") + len(chunks).to_bytes(2, "little") + b"".join(len(c).to_bytes(4, "little") + c for c in chunks)


def block(out_size, in_size, payload):
    return out_size.to_bytes(2, "little") + in_size.to_bytes(2, "little") + payload


class BasicTests(unittest.TestCase):
    def test_run_chunk(self):
        out = parse(pack([(4).to_bytes(2, "little") + (5).to_bytes(2, "little") + bytes([0xC3])]))
        self.assertEqual(out["chunks"], [4])
        self.assertEqual(out["sum"], 0)

    def test_bad_magic(self):
        with self.assertRaises(SndError):
            parse(b"VOCPACK!" + bytes(8))


class DriverTests(unittest.TestCase):
    def test_2bit_samples_are_low_pair_first(self):
        # One command byte: code 0, count 0, one coded byte 0xE4.
        # Driver order (pairs 0,1,2,3 of the byte) gives 0,1,2,3:
        # 128 -> 126 -> 125 -> 125 -> 126. High-pair-first would reverse
        # the four samples and play 126, 125, 125, 126 backwards.
        self.assertEqual(chunk(block(4, 5, bytes([0x00, 0xE4]))),
                         bytes([126, 125, 125, 126]))

    def test_4bit_uses_step_table_and_low_nibble_first(self):
        # One byte 0xF0: low nibble 0 -> -9, then high nibble 15 -> +8.
        self.assertEqual(chunk(block(2, 5, bytes([0x40, 0xF0]))),
                         bytes([119, 127]))

    def test_big_delta_is_unsigned_and_upward(self):
        # Code 2 with bit 5 set is one unsigned big delta (0..31).
        # 0xB0 -> data 0x10 -> +16; 0xAF -> data 0x0F -> +15.
        self.assertEqual(chunk(block(1, 4, bytes([0xB0]))), bytes([144]))
        self.assertEqual(chunk(block(1, 4, bytes([0xAF]))), bytes([143]))
        # A second big delta chains from the moved level.
        self.assertEqual(chunk(block(2, 6, bytes([0xB0, 0xB0]))),
                         bytes([144, 160]))

    def test_each_chunk_restarts_at_silence(self):
        # A big delta moves the first chunk, the next one must start at 128.
        first = block(1, 4, bytes([0xAF]))            # 128 + 15 = 143
        second = block(1, 4, bytes([0xAF]))           # restarts: 128 + 15 = 143
        out = parse(pack([first, second]))
        self.assertEqual(out["chunks"], [1, 1])
        self.assertEqual(out["sum"], 2 * (143 - 128))

    def test_raw_chunk_is_copied_when_sizes_match(self):
        raw = bytes([0, 1, 128, 200, 255])
        self.assertEqual(chunk(block(5, 5, raw)), raw)

    def test_truncated_block_is_rejected_not_crashed(self):
        # Code 0, count 1 asks for two coded bytes but only one follows.
        with self.assertRaises(SndError):
            chunk(block(8, 6, bytes([0x01, 0xE4])))
        # The same bad block inside a pack is reported as a null chunk.
        out = parse(pack([block(8, 6, bytes([0x01, 0xE4]))]))
        self.assertEqual(out["chunks"], [None])


if __name__ == "__main__":
    unittest.main()
