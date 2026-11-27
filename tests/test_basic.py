import unittest

from c140kit.voices import CLOCK, C140Error, TABLE, parse

HDR = 8 + 24 * 16
BANK_WORDS = 1 << 16


def be(v):
    return bytes(((v >> 8) & 0xff, v & 0xff))


def block(mode, start=0, end=4, loop=0, freq=0x1000, right=0, left=0):
    # snapshot block layout, 16 bytes / 8 byte-pairs:
    # 0 right/left (one 8-bit volume byte each), 2 freq, 4 control/bank,
    # 6 start, 8 end, 10 loop, 12 unused, 14 unused
    return (bytes((right, left)) + be(freq) + be(mode)
            + be(start) + be(end) + be(loop) + b"\x00\x00" + b"\x00\x00")


def snapshot(blocks, rom):
    regs = b"".join(blocks) + bytes((24 - len(blocks)) * 16)
    return b"C140SNAP" + regs + rom


def s12(v):
    v &= 0xfff
    return v - 0x1000 if v & 0x800 else v


class BasicTests(unittest.TestCase):
    def test_no_voice_keyed(self):
        out = parse(b"C140SNAP" + bytes(24 * 16) + bytes(512))
        self.assertEqual(out["voices_keyed"], 0)
        self.assertEqual(out["rom_words"], 256)

    def test_not_a_snapshot(self):
        with self.assertRaises(C140Error):
            parse(b"C140SNAX" + bytes(64))

    def test_banking_reaches_second_rom_bank(self):
        # keyed voice with bank select = 1; its data must be read from bank 1
        rom = bytearray(2 * BANK_WORDS * 2)
        addr = BANK_WORDS + 0x100
        rom[2 * addr] = 0x12
        rom[2 * addr + 1] = 0x34
        data = snapshot([block(0x0180, start=0x100, end=0x101, freq=0)], bytes(rom))
        v = parse(data)["voices"][0]
        self.assertEqual(v["address"], BANK_WORDS + 0x100)
        self.assertEqual(v["peak"], abs(s12(0x1234)))
        self.assertEqual(v["sum"], s12(0x1234))
        self.assertTrue(v["complete"])

    def test_linear_is_signed_12bit_big_endian(self):
        words = [0x0001, 0x0fff, 0x0800, 0x0abc]
        rom = b"".join(be(w) for w in words)
        data = snapshot([block(0x0080, start=0, end=len(words), freq=0)], rom)
        v = parse(data)["voices"][0]
        self.assertEqual(v["format"], "linear")
        self.assertEqual(v["peak"], max(abs(s12(w)) for w in words))
        self.assertEqual(v["sum"], sum(s12(w) for w in words))

    def test_compressed_sample_is_high_byte(self):
        # low byte is ROM padding and must be ignored
        codes = [0x00, 0x7f, 0x80, 0xff]
        rom = b"".join(bytes((c, 0x3a)) for c in codes)
        data = snapshot([block(0x0088, start=0, end=len(codes), freq=0)], rom)
        v = parse(data)["voices"][0]
        expected = [TABLE[c] for c in codes]
        self.assertEqual(v["format"], "compressed")
        self.assertEqual(v["sum"], sum(expected))
        self.assertEqual(v["peak"], max(abs(x) for x in expected))

    def test_rate_uses_32768_divisor(self):
        data = snapshot([block(0x0080, freq=0x2000, start=0, end=1)], bytes(4))
        v = parse(data)["voices"][0]
        self.assertEqual(v["rate_hz"], "%.2f" % (CLOCK * 0x2000 / 32768))

    def test_loop_address_is_banked(self):
        data = snapshot([block(0x0190, start=0x10, end=0x20, loop=0x18, freq=0)],
                        bytes(2 * BANK_WORDS * 2))
        v = parse(data)["voices"][0]
        self.assertEqual(v["loop_start"], BANK_WORDS + 0x18)

    def test_incomplete_when_past_rom_end(self):
        data = snapshot([block(0x0080, start=0x10, end=0x20, freq=0)], bytes(0x18 * 2))
        v = parse(data)["voices"][0]
        self.assertFalse(v["complete"])


if __name__ == "__main__":
    unittest.main()
