import struct
import unittest
import zlib

from gymkit.gym import parse

LOG = b"\x01\x22\x00\x02\xa4\x22\x00\x00"


def field(value, size):
    return value.encode("latin-1").ljust(size, b"\0")


def gymx(body, song=b"Song", loop_frame=1, packed=True):
    header = b"GYMX" + b"".join([
        field("Song", 32), field("Game", 32), field("Publisher", 32),
        field("Gens", 32), field("Dumper", 32), field("", 256),
    ])
    data = zlib.compress(body) if packed else body
    size = len(body) if packed else 0
    return header + struct.pack("<II", loop_frame, size) + data


class BasicTests(unittest.TestCase):
    def test_writes(self):
        out = parse(LOG)
        self.assertEqual((out["ym_port0_writes"], out["ym_port1_writes"], out["frames"]), (1, 1, 2))

    def test_no_header(self):
        self.assertIsNone(parse(LOG)["header"])


class HeaderTests(unittest.TestCase):
    def test_strings(self):
        head = parse(gymx(b"\x00"))["header"]
        self.assertEqual(
            (head["song"], head["game"], head["publisher"], head["emulator"], head["dumper"], head["comment"]),
            ("Song", "Game", "Publisher", "Gens", "Dumper", None),
        )

    def test_loop_and_packed(self):
        head = parse(gymx(b"\x00", loop_frame=121))["header"]
        self.assertEqual((head["loop_frame"], head["packed"]), (121, True))
        head = parse(gymx(b"\x00", loop_frame=0, packed=False))["header"]
        self.assertEqual((head["loop_frame"], head["packed"]), (None, False))

    def test_packed_body_is_read(self):
        body = b"\x01\x2a\x80" + b"\x00" * 4 + b"\x03\x9f"
        out = parse(gymx(body))
        self.assertEqual((out["frames"], out["dac_writes"], out["psg_writes"]), (4, 1, 1))

    def test_unpacked_body_is_read(self):
        body = b"\x01\x2a\x80" + b"\x00" * 4
        out = parse(gymx(body, packed=False))
        self.assertEqual((out["frames"], out["dac_writes"]), (4, 1))


class TimingTests(unittest.TestCase):
    def test_duration_at_60hz(self):
        self.assertEqual(parse(b"\x00" * 60)["duration"], "1")
        self.assertEqual(parse(b"\x00" * 90)["duration"], "3/2")

    def test_loop_seconds(self):
        self.assertEqual(parse(gymx(b"\x00" * 120, loop_frame=121))["loop_seconds"], "2")
        self.assertIsNone(parse(LOG)["loop_seconds"])
        self.assertIsNone(parse(gymx(b"\x00", loop_frame=0))["loop_seconds"])


class PsgTests(unittest.TestCase):
    def test_psg_is_one_byte(self):
        out = parse(b"\x03\x9f" + b"\x01\x22\x00")
        self.assertEqual((out["psg_writes"], out["ym_port0_writes"]), (1, 1))

    def test_psg_does_not_shift_following_commands(self):
        out = parse(b"\x03\x80" + b"\x01\x28\xf0" + b"\x02\x28\xf5" + b"\x03\x8f")
        self.assertEqual((out["psg_writes"], out["ym_port0_writes"], out["ym_port1_writes"]), (2, 1, 1))


class KeyOnTests(unittest.TestCase):
    def test_channels(self):
        values = (0xF0, 0xF1, 0xF2, 0xF4, 0xF5, 0xF6)
        out = parse(b"".join(b"\x01\x28" + bytes([v]) for v in values))
        self.assertEqual(out["key_ons"], {"1": 1, "2": 1, "3": 1, "4": 1, "5": 1, "6": 1})

    def test_second_bank_is_channels_four_to_six(self):
        out = parse(b"\x01\x28\xf4")
        self.assertEqual(out["key_ons"], {"4": 1})

    def test_key_off_ignored(self):
        self.assertEqual(parse(b"\x01\x28\x00")["key_ons"], {})

    def test_port1_key_on_ignored(self):
        self.assertEqual(parse(b"\x02\x28\xf0")["key_ons"], {})
