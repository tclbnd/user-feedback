import struct
import unittest

from aykit.ay import parse

HEAD = b"ZXAYEMUL" + bytes([3, 1]) + b"\0\0\0\0\0\0" + bytes([0, 0]) + b"\0\0"


class BasicTests(unittest.TestCase):
    def test_versions(self):
        out = parse(HEAD)
        self.assertEqual((out["file_version"], out["player_version"], out["first_song"]), (3, 1, 0))

    def test_not_ay(self):
        with self.assertRaises(ValueError):
            parse(b"ZXAYxxxx" + bytes(20))

    def test_full_parse(self):
        data = bytearray(0xA0 + 0x800)
        data[0:8] = b"ZXAYEMUL"
        data[8], data[9] = 3, 1
        struct.pack_into(">h", data, 12, 0x30 - 12)          # PAuthor
        struct.pack_into(">h", data, 14, 0x40 - 14)          # PMisc
        data[16], data[17] = 0, 0                            # one song, first song 0
        struct.pack_into(">h", data, 18, 0x20 - 18)          # PSongsStructure
        struct.pack_into(">h", data, 0x20, 0x50 - 0x20)      # PSongName
        struct.pack_into(">h", data, 0x22, 0x60 - 0x22)      # PSongData
        data[0x30:0x35] = b"Auth\0"
        data[0x40:0x45] = b"Misc\0"
        data[0x50:0x55] = b"Song\0"
        data[0x60:0x64] = bytes([0, 1, 2, 3])                # A, B, C, noise
        struct.pack_into(">H", data, 0x64, 100)              # length: 100/50 s
        struct.pack_into(">H", data, 0x66, 25)               # fade: 25/50 s
        data[0x68], data[0x69] = 0x12, 0x34                  # hi/lo reg
        struct.pack_into(">h", data, 0x6A, 0x80 - 0x6A)      # PPoints
        struct.pack_into(">h", data, 0x6C, 0x90 - 0x6C)      # PAddresses
        struct.pack_into(">HHH", data, 0x80, 0xFFF0, 0x8000, 0x8006)
        # block at $FC00, length $800: only $400 bytes fit below $10000
        struct.pack_into(">HH", data, 0x90, 0xFC00, 0x800)
        struct.pack_into(">h", data, 0x94, 0xA0 - 0x94)
        struct.pack_into(">HHh", data, 0x96, 0, 0, 0)        # terminator
        out = parse(bytes(data))
        self.assertEqual((out["author"], out["misc"], out["songs"]), ("Auth", "Misc", 1))
        (song,) = out["song_list"]
        self.assertEqual(song["name"], "Song")
        self.assertEqual(song["channels"], [0, 1, 2, 3])
        self.assertEqual((song["length_seconds"], song["fade_seconds"]), ("2", "1/2"))
        self.assertEqual((song["hi_reg"], song["lo_reg"]), (0x12, 0x34))
        self.assertEqual((song["stack"], song["init"], song["interrupt"]),
                         ("$FFF0", "$8000", "$8006"))
        self.assertEqual((song["blocks"], song["block_bytes"]), (1, 0x400))
