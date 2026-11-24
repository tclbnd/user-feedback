import struct
import unittest
import zlib

from gymkit.gym import GymError, parse

LOG = b"\x01\x22\x00\x02\xa4\x22\x00\x00"


def header(song=b"Song", game=b"Game", publisher=b"Pub", emulator=b"Gens",
           dumper=b"Ripper", comment=b"", loop=0, packed_size=0):
    fields = (song, game, publisher, emulator, dumper)
    head = b"GYMX" + b"".join(f.ljust(32, b"\0") for f in fields)
    head += comment.ljust(256, b"\0")
    head += struct.pack("<II", loop, packed_size)
    assert len(head) == 0x1AC
    return head


def ym(port, reg, val):
    return bytes((port, reg, val))


def psg(byte):
    return bytes((0x03, byte))


class BasicTests(unittest.TestCase):
    def test_writes(self):
        out = parse(LOG)
        self.assertEqual((out["ym_port0_writes"], out["ym_port1_writes"], out["frames"]), (1, 1, 2))

    def test_no_header(self):
        self.assertIsNone(parse(LOG)["header"])


class HeaderTests(unittest.TestCase):
    def test_fields(self):
        out = parse(header(song=b"Green Hill Zone", game=b"Sonic", publisher=b"Sega",
                           emulator=b"Gens", dumper=b"Ripper") + LOG)
        head = out["header"]
        self.assertEqual(head["song"], "Green Hill Zone")
        self.assertEqual(head["game"], "Sonic")
        self.assertEqual(head["publisher"], "Sega")
        self.assertEqual(head["emulator"], "Gens")
        self.assertEqual(head["dumper"], "Ripper")
        self.assertIsNone(head["comment"])
        self.assertIsNone(head["loop_frame"])
        self.assertFalse(head["packed"])

    def test_comment_and_loop(self):
        out = parse(header(comment=b"recorded at 60 Hz", loop=121) + LOG)
        self.assertEqual(out["header"]["comment"], "recorded at 60 Hz")
        self.assertEqual(out["header"]["loop_frame"], 121)
        self.assertEqual(out["loop_seconds"], "2")

    def test_no_loop(self):
        self.assertIsNone(parse(header() + LOG)["loop_seconds"])

    def test_packed(self):
        raw = LOG * 4
        out = parse(header(packed_size=len(raw)) + zlib.compress(raw))
        self.assertTrue(out["header"]["packed"])
        self.assertEqual(out["frames"], 8)
        self.assertEqual(out["ym_port0_writes"], 4)

    def test_unpacked_body_after_header(self):
        out = parse(header() + LOG)
        self.assertFalse(out["header"]["packed"])
        self.assertEqual(out["frames"], 2)

    def test_truncated_header(self):
        with self.assertRaises(GymError):
            parse(b"GYMX" + b"\0" * 10)

    def test_corrupt_packed_body(self):
        with self.assertRaises(GymError):
            parse(header() + b"\x78\x9c\xff\xff\xff")


class TimingTests(unittest.TestCase):
    def test_frame_is_sixtieth_of_a_second(self):
        out = parse(b"\x00" * 600)
        self.assertEqual(out["frames"], 600)
        self.assertEqual(out["duration"], "10")

    def test_duration_is_a_fraction(self):
        self.assertEqual(parse(b"\x00")["duration"], "1/60")


class KeyOnTests(unittest.TestCase):
    def test_all_six_channels(self):
        body = b"".join(ym(0x01, 0x28, 0xF0 | (ch - 1)) for ch in range(1, 7))
        out = parse(body)
        self.assertEqual(out["key_ons"], {str(ch): 1 for ch in range(1, 7)})

    def test_operators_in_low_banks_are_counted(self):
        out = parse(ym(0x01, 0x28, 0x02))     # channel 3, operator bits clear
        self.assertEqual(out["key_ons"]["3"], 0)

    def test_key_off_not_counted(self):
        out = parse(ym(0x01, 0x28, 0x00) + ym(0x01, 0x28, 0x03))
        self.assertEqual(sum(out["key_ons"].values()), 0)

    def test_invalid_channel_code_ignored(self):
        out = parse(ym(0x01, 0x28, 0xF7) + ym(0x01, 0x28, 0xFF))
        self.assertEqual(sum(out["key_ons"].values()), 0)

    def test_repeated_key_ons_counted_individually(self):
        out = parse(ym(0x01, 0x28, 0xF5) * 3)
        self.assertEqual(out["key_ons"]["6"], 3)


class PsgTests(unittest.TestCase):
    def test_command_reads_one_byte(self):
        out = parse(psg(0x80) + psg(0x9F))
        self.assertEqual(out["psg_writes"], 2)
        self.assertEqual(out["ym_port0_writes"], 0)

    def test_volume_writes_per_channel(self):
        out = parse(psg(0x9F) + psg(0xBF) + psg(0xDF) + psg(0xFF))
        self.assertEqual(out["psg_volume_writes"], {"0": 1, "1": 1, "2": 1, "3": 1})

    def test_noise_channel_volume(self):
        out = parse(psg(0xF3))
        self.assertEqual(out["psg_volume_writes"]["3"], 1)
        self.assertEqual(out["psg_volumes"]["3"], 3)

    def test_tone_data_not_counted_as_volume(self):
        out = parse(psg(0x85) + psg(0x0A))
        self.assertEqual(out["psg_volume_writes"], {"0": 0, "1": 0, "2": 0, "3": 0})

    def test_volumes_start_silent(self):
        self.assertEqual(parse(b"")["psg_volumes"], {"0": 15, "1": 15, "2": 15, "3": 15})

    def test_volume_tracks_last_write(self):
        out = parse(psg(0x9F) + psg(0x90))
        self.assertEqual(out["psg_volumes"]["0"], 0)
        self.assertEqual(out["psg_volume_writes"]["0"], 2)


class DacTests(unittest.TestCase):
    def test_data_writes(self):
        out = parse(ym(0x01, 0x2A, 0x80) * 5)
        self.assertEqual(out["dac_writes"], 5)

    def test_port1_data_not_counted(self):
        self.assertEqual(parse(ym(0x02, 0x2A, 0x80) * 5)["dac_writes"], 0)

    def test_disabled_by_default(self):
        self.assertFalse(parse(b"")["dac_enabled"])

    def test_enabled_at_end(self):
        self.assertTrue(parse(ym(0x01, 0xB6, 0x80))["dac_enabled"])

    def test_disabled_again(self):
        out = parse(ym(0x01, 0xB6, 0x80) + ym(0x01, 0xB6, 0x00))
        self.assertFalse(out["dac_enabled"])

    def test_enable_register_with_bit_clear(self):
        self.assertFalse(parse(ym(0x01, 0xB6, 0x40))["dac_enabled"])
