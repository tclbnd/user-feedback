import struct
import unittest
import zlib

from gymkit.gym import GymError, GYMX_SIZE, parse

LOG = b"\x01\x22\x00\x02\xa4\x22\x00\x00"


def gymx(fields, body, packed=True):
    """Build a GYMX file from the five text fields, comment and loop frame."""
    song, game, publisher, emulator, dumper, comment, loop_frame = fields
    raw = struct.pack("<32s32s32s32s32s256sII",
                      song.encode(), game.encode(), publisher.encode(),
                      emulator.encode(), dumper.encode(), comment.encode(),
                      loop_frame, len(body))
    header = b"GYMX" + raw
    return header + (zlib.compress(body) if packed else body)


class BasicTests(unittest.TestCase):
    def test_writes(self):
        out = parse(LOG)
        self.assertEqual((out["ym_port0_writes"], out["ym_port1_writes"], out["frames"]), (1, 1, 2))

    def test_no_header(self):
        self.assertIsNone(parse(LOG)["header"])

    def test_timing_is_60_hz(self):
        out = parse(b"\x00" * 60)
        self.assertEqual(out["frames"], 60)
        self.assertEqual(out["duration"], "1")
        self.assertEqual(parse(b"\x00\x00")["duration"], "1/30")

    def test_psg_command_is_one_byte(self):
        # One PSG write followed by one frame wait: a two-byte PSG command
        # would swallow the frame and mis-count everything after it.
        out = parse(b"\x03\x9f\x00\x01\x28\xf0\x00")
        self.assertEqual(out["psg_writes"], 1)
        self.assertEqual(out["frames"], 2)
        self.assertEqual(out["ym_port0_writes"], 1)

    def test_psg_volume_writes_per_channel_including_noise(self):
        # Latch nibble is the SN76489 register number:
        # 0x8/0x9 = ch0 tone/volume, 0xA/0xB = ch1, 0xC/0xD = ch2,
        # 0xE/0xF = ch3 (noise); the low nibble is data (attenuation).
        stream = bytes((0x03, 0x97,  # ch 0 volume 7
                        0x03, 0xa0,  # ch 1 tone latch (bit 4 clear)
                        0x03, 0x12,  # PSG data byte (no latch)
                        0x03, 0xd0,  # ch 2 volume 0
                        0x03, 0xff,  # ch 3 (noise) volume 15
                        0x03, 0xe6))  # ch 3 noise control (bit 4 clear)
        out = parse(stream)
        self.assertEqual(out["psg_writes"], 6)
        self.assertEqual(out["psg_volume_writes"],
                         {"0": 1, "1": 0, "2": 1, "3": 1})
        self.assertEqual(out["psg_volumes"], [7, 15, 0, 15])

    def test_psg_volumes_start_muted(self):
        self.assertEqual(parse(b"\x00")["psg_volumes"], [15, 15, 15, 15])

    def test_key_ons_separate_channels_and_reject_invalid_codes(self):
        # Key-on (0xF0) for channels 1..6 plus the invalid codes 6 and 7.
        stream = b"".join(b"\x01\x28" + bytes((v,))
                          for v in (0xF0, 0xF1, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7))
        # Key-off values must never be counted.
        stream += b"\x01\x28\x00\x01\x28\x05"
        out = parse(stream)
        self.assertEqual(out["key_ons"],
                         {"1": 1, "2": 1, "3": 1, "4": 1, "5": 1, "6": 1})

    def test_dac_writes_and_enable_state(self):
        # Two DAC sample writes (reg 0x2A); DAC starts disabled.
        stream = b"\x01\x2a\x55\x01\x2a\xaa"
        out = parse(stream)
        self.assertEqual(out["dac_writes"], 2)
        self.assertFalse(out["dac_enabled"])
        # Bit 7 of reg 0x27 enables the DAC; a later write can disable it.
        out = parse(stream + b"\x01\x27\x80\x01\x27\x00")
        self.assertFalse(out["dac_enabled"])
        out = parse(stream + b"\x01\x27\x80")
        self.assertTrue(out["dac_enabled"])
        self.assertEqual(out["dac_writes"], 2)

    def test_gymx_packed_header_and_loop(self):
        body = b"\x01\x22\x00" + b"\x00" * 120 + b"\x03\x9f"
        data = gymx(("Song", "Game", "Pub", "Gens", "Me", "A note", 121), body)
        out = parse(data)
        self.assertEqual(out["header"], {
            "song": "Song", "game": "Game", "publisher": "Pub",
            "emulator": "Gens", "dumper": "Me", "comment": "A note",
            "loop_frame": 121, "packed": True})
        self.assertEqual(out["frames"], 120)
        # Loop frame is 1-based, so frame 121 starts 120 frames in.
        self.assertEqual(out["loop_seconds"], "2")

    def test_gymx_unpacked_body_and_zero_loop(self):
        body = b"\x00\x00\x00"
        data = gymx(("", "", "", "", "", "", 0), body, packed=False)
        self.assertEqual(len(data), GYMX_SIZE + 3)
        out = parse(data)
        self.assertFalse(out["header"]["packed"])
        self.assertIsNone(out["header"]["loop_frame"])
        self.assertIsNone(out["header"]["song"])
        self.assertEqual(out["frames"], 3)
        self.assertIsNone(out["loop_seconds"])

    def test_unknown_command_and_truncation(self):
        with self.assertRaises(GymError):
            parse(b"\x05\x00")
        with self.assertRaises(GymError):
            parse(b"\x01\x28")
        with self.assertRaises(GymError):
            parse(b"\x03")


class ExampleTests(unittest.TestCase):
    def test_examples(self):
        with open("examples/boss.gym", "rb") as fh:
            boss = parse(fh.read())
        self.assertIsNone(boss["header"])
        self.assertEqual(boss["frames"], 303)
        self.assertEqual(boss["duration"], "101/20")
        self.assertEqual((boss["ym_port0_writes"], boss["ym_port1_writes"],
                          boss["psg_writes"], boss["dac_writes"]),
                         (101, 300, 11, 75))
        self.assertFalse(boss["dac_enabled"])
        self.assertEqual(boss["psg_volume_writes"],
                         {"0": 1, "1": 0, "2": 0, "3": 0})
        self.assertEqual(boss["psg_volumes"], [15, 15, 15, 15])
        self.assertEqual(boss["key_ons"],
                         {"1": 10, "2": 1, "3": 1, "5": 1, "6": 10})
        self.assertIsNone(boss["loop_seconds"])

        with open("examples/green_hill.gym", "rb") as fh:
            green = parse(fh.read())
        self.assertTrue(green["header"]["packed"])
        self.assertEqual(green["header"]["song"], "Green Hill Zone")
        self.assertEqual(green["header"]["game"], "Sonic the Hedgehog")
        self.assertEqual(green["header"]["publisher"], "Sega")
        self.assertEqual(green["header"]["emulator"], "Gens")
        self.assertEqual(green["header"]["dumper"], "Ripper")
        self.assertEqual(green["header"]["loop_frame"], 121)
        self.assertEqual(green["frames"], 600)
        self.assertEqual(green["duration"], "10")
        self.assertEqual(green["loop_seconds"], "2")
        self.assertEqual(green["dac_writes"], 150)
        self.assertFalse(green["dac_enabled"])
