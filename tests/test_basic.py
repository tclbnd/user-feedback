import os
import unittest

from okikit.rom import RomError, _decode, parse


def make_rom(entries, size=0x1000, fill=0):
    """Build a ROM with the given {phrase: (start, end)} table entries."""
    data = bytearray([fill]) * size
    for num, (start, end) in entries.items():
        data[8 * num:8 * num + 8] = bytes([start >> 16 & 0xFF, start >> 8 & 0xFF, start & 0xFF,
                                          end >> 16 & 0xFF, end >> 8 & 0xFF, end & 0xFF, 0, 0])
    return bytes(data)


EXAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")


class BasicTests(unittest.TestCase):
    def test_empty_table(self):
        out = parse(bytes(0x1000))
        self.assertEqual(out["phrases"], 0)

    def test_too_small(self):
        with self.assertRaises(RomError):
            parse(bytes(100))


class TableTests(unittest.TestCase):
    def test_addresses_are_big_endian(self):
        out = parse(make_rom({1: (0x012345, 0x06789A)}))
        item = out["phrase_list"][0]
        self.assertEqual(item["phrase"], 1)
        self.assertEqual(item["start"], "$12345")
        self.assertEqual(item["end"], "$6789A")

    def test_end_address_is_inclusive(self):
        data = bytearray(make_rom({2: (0x400, 0x407)}))
        data[0x400:0x408] = b"\x80" * 8
        item = parse(bytes(data))["phrase_list"][0]
        self.assertEqual(item["bytes"], 8)
        self.assertEqual(item["samples"], 16)

    def test_undefined_entries_are_not_listed(self):
        # Only a complete start/end pair defines a playable phrase.
        for start, end in ((0, 0), (0, 0x500), (0x500, 0)):
            out = parse(make_rom({4: (start, end)}))
            self.assertEqual(out["phrases"], 0, (start, end))

    def test_phrase_numbers_keep_table_positions(self):
        out = parse(make_rom({1: (0x400, 0x40F), 3: (0x420, 0x42F)}))
        self.assertEqual([p["phrase"] for p in out["phrase_list"]], [1, 3])

    def test_out_of_range_entry_is_invalid(self):
        for start, end in ((0x400, 0x1000), (0x400, 0x3FF)):
            item = parse(make_rom({1: (start, end)}))["phrase_list"][0]
            self.assertFalse(item["valid"])
            self.assertIsNone(item["bytes"])
            self.assertIsNone(item["samples"])
            self.assertIsNone(item["peak"])
            self.assertIsNone(item["last_sample"])

    def test_last_byte_of_rom_is_playable(self):
        item = parse(make_rom({1: (0xFF0, 0xFFF)}))["phrase_list"][0]
        self.assertTrue(item["valid"])
        self.assertEqual(item["bytes"], 16)


class DecodeTests(unittest.TestCase):
    def test_high_nibble_comes_first(self):
        # 0x07: high nibble 0 then high nibble 7 -> 2 then 32.
        self.assertEqual(_decode(b"\x07"), (2, 32, 32))

    def test_samples_saturate_at_twelve_bits(self):
        n, peak, last = _decode(b"\x77" * 32)
        self.assertEqual(n, 64)
        self.assertEqual(peak, 2047)
        self.assertEqual(last, 2047)

    def test_negative_samples_saturate_at_twelve_bits(self):
        n, peak, last = _decode(b"\xFF" * 32)
        self.assertEqual(n, 64)
        self.assertEqual(peak, 2048)
        self.assertEqual(last, -2048)

    def test_silence_code_stays_near_zero(self):
        n, peak, last = _decode(b"\x80" * 16)
        self.assertEqual(n, 32)
        self.assertLess(peak, 64)


class ExampleTests(unittest.TestCase):
    def _parse(self, name):
        with open(os.path.join(EXAMPLE, name), "rb") as fh:
            return parse(fh.read())

    def test_fighter(self):
        out = self._parse("fighter.oki")
        self.assertEqual(out["size"], 65536)
        self.assertEqual([p["phrase"] for p in out["phrase_list"]], [1, 2, 3])
        first = out["phrase_list"][0]
        self.assertEqual((first["start"], first["end"]), ("$00400", "$00BCF"))
        self.assertEqual(first["bytes"], 2000)
        self.assertEqual(first["samples"], 4000)
        self.assertEqual(first["peak"], "0.894043")
        self.assertEqual(first["last_sample"], 1632)
        self.assertTrue(first["valid"])

    def test_shooter(self):
        out = self._parse("shooter.oki")
        self.assertEqual([p["phrase"] for p in out["phrase_list"]], list(range(1, 8)))
        self.assertTrue(all(p["valid"] for p in out["phrase_list"]))
        self.assertEqual([p["bytes"] for p in out["phrase_list"]], [350, 400, 450, 500, 550, 600, 650])
