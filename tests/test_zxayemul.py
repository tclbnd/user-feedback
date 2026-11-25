import os
import struct
import unittest

from aykit.ay import parse

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(os.path.dirname(HERE), "examples")


def load(name):
    with open(os.path.join(EXAMPLES, name), "rb") as fh:
        return parse(fh.read())


class Builder:
    """Assemble a ZXAYEMUL file with relative pointers."""

    def __init__(self):
        self.data = bytearray(20)
        self.data[:8] = b"ZXAYEMUL"
        self.data[8] = 3
        self.data[9] = 1
        self.patches = []
        self.shared_blocks = {}

    def add(self, blob):
        at = len(self.data)
        self.data += blob
        return at

    def point(self, field, target):
        self.patches.append((field, target))

    def add_string(self, text, field):
        if text is not None:
            self.point(field, self.add(text.encode("latin-1") + b"\0"))

    def add_blocks(self, spec):
        at = self.add(bytes(6 * (len(spec) + 1)))   # plus the 0-address sentinel
        p = at
        for addr, length, payload in spec:
            struct.pack_into(">HH", self.data, p, addr, length)
            if payload is not None:
                self.point(p + 4, self.add(payload))
            p += 6
        return at

    def blocks_for(self, spec, key=None):
        if key is None:
            return self.add_blocks(spec)
        if key not in self.shared_blocks:
            self.shared_blocks[key] = self.add_blocks(spec)
        return self.shared_blocks[key]

    def finish(self, songs, author=None, misc=None, first_song=0):
        self.data[16] = len(songs) - 1
        self.data[17] = first_song
        self.add_string(author, 12)
        self.add_string(misc, 14)
        table = self.add(bytes(4 * len(songs)))
        self.point(18, table)
        for i, song in enumerate(songs):
            entry = table + 4 * i
            self.add_string(song.get("name"), entry)
            record = self.add(bytes(14))
            self.point(entry + 2, record)
            self.data[record:record + 4] = bytes(song["channels"])
            struct.pack_into(">HH", self.data, record + 4,
                             song.get("length", 0), song.get("fade", 0))
            self.data[record + 8] = song.get("hi", 0)
            self.data[record + 9] = song.get("lo", 0)
            points = song.get("points")
            if points is not None:
                pts = self.add(struct.pack(">HHH", *points))
                self.point(record + 10, pts)
            blocks = song.get("blocks")
            if blocks is not None:
                at = self.blocks_for(blocks, song.get("blocks_key"))
                self.point(record + 12, at)
        for field, target in self.patches:
            struct.pack_into(">h", self.data, field, target - field)
        return bytes(self.data)


class AgentXTests(unittest.TestCase):
    def setUp(self):
        self.out = load("agent_x.ay")

    def test_header(self):
        self.assertEqual((self.out["file_version"], self.out["player_version"]), (3, 0))
        self.assertEqual((self.out["author"], self.out["misc"]),
                         ("Tim Follin", "Converted with AYMakeR"))
        self.assertEqual((self.out["songs"], self.out["first_song"]), (1, 0))

    def test_song(self):
        s = self.out["song_list"][0]
        self.assertEqual(s["name"], "Agent X II - Level 1")
        self.assertEqual(s["channels"], [0, 1, 2, 3])
        self.assertEqual((s["length_seconds"], s["fade_seconds"]), ("240", "5"))
        self.assertEqual((s["hi_reg"], s["lo_reg"]), (0, 0))
        self.assertEqual((s["stack"], s["init"], s["interrupt"]),
                         ("$FFF0", "$8000", "$8006"))
        self.assertEqual((s["blocks"], s["block_bytes"]), (2, 2500))


class CompilationTests(unittest.TestCase):
    def setUp(self):
        self.out = load("compilation.ay")

    def test_header(self):
        self.assertEqual((self.out["author"], self.out["misc"]), ("Various", None))
        self.assertEqual((self.out["songs"], self.out["first_song"]), (3, 1))
        self.assertEqual(len(self.out["song_list"]), 3)

    def test_title_channels_and_regs(self):
        s = self.out["song_list"][0]
        self.assertEqual(s["name"], "Title")
        self.assertEqual(s["channels"], [0, 1, 2, 3])
        self.assertEqual((s["hi_reg"], s["lo_reg"]), (1, 2))
        # The block claims 8000 bytes at $F000: only the 4096 inside the Z80 space load.
        self.assertEqual((s["blocks"], s["block_bytes"]), (1, 4096))

    def test_in_game_mapping(self):
        s = self.out["song_list"][1]
        self.assertEqual(s["name"], "In Game")
        self.assertEqual(s["channels"], [2, 1, 0, 3])
        self.assertEqual((s["length_seconds"], s["fade_seconds"]), ("120", "3"))
        self.assertEqual((s["blocks"], s["block_bytes"]), (1, 64))

    def test_song_without_player_points(self):
        s = self.out["song_list"][2]
        self.assertIsNone(s["name"])
        self.assertEqual((s["stack"], s["init"], s["interrupt"]), (None, None, None))
        self.assertEqual((s["blocks"], s["block_bytes"]), (1, 64))


class SyntheticTests(unittest.TestCase):
    @staticmethod
    def one(**song):
        song.setdefault("channels", [0, 1, 2, 3])
        return parse(Builder().finish([song]))["song_list"][0]

    def test_init_zero_uses_interrupt(self):
        s = self.one(points=(0xFFF0, 0, 0x9000))
        self.assertEqual(s["init"], "$9000")
        self.assertEqual(s["interrupt"], "$9000")

    def test_points_are_big_endian(self):
        s = self.one(points=(0x1234, 0x5678, 0x9ABC))
        self.assertEqual((s["stack"], s["init"], s["interrupt"]),
                         ("$1234", "$5678", "$9ABC"))

    def test_unknown_and_fractional_lengths(self):
        self.assertIsNone(self.one(length=0, fade=0)["length_seconds"])
        s = self.one(length=25, fade=75)
        self.assertEqual((s["length_seconds"], s["fade_seconds"]), ("1/2", "3/2"))

    def test_high_low_registers(self):
        s = self.one(hi=7, lo=11)
        self.assertEqual((s["hi_reg"], s["lo_reg"]), (7, 11))

    def test_block_truncated_by_z80_space(self):
        s = self.one(blocks=[(0xFFF0, 0x20, b"\1" * 0x20)])
        self.assertEqual((s["blocks"], s["block_bytes"]), (1, 0x10))

    def test_block_truncated_by_file_end(self):
        s = self.one(blocks=[(0x8000, 0x100, b"\1" * 4)])
        self.assertEqual((s["blocks"], s["block_bytes"]), (1, 4))

    def test_block_without_data_not_counted(self):
        s = self.one(blocks=[(0x8000, 0x100, None)])
        self.assertEqual((s["blocks"], s["block_bytes"]), (0, 0))

    def test_songs_sharing_one_block_table(self):
        data = Builder().finish([
            {"channels": [0, 1, 2, 3], "blocks": [(0x8000, 4, b"\0" * 4)], "blocks_key": "k"},
            {"channels": [3, 2, 1, 0], "blocks": [(0x8000, 4, b"\0" * 4)], "blocks_key": "k"},
        ])
        songs = parse(data)["song_list"]
        self.assertEqual([(s["blocks"], s["block_bytes"]) for s in songs], [(1, 4), (1, 4)])

    def test_no_song_table(self):
        data = bytearray(20)
        data[:8] = b"ZXAYEMUL"
        data[8], data[9] = 3, 1
        data[16] = 0           # one song claimed, but no song table pointer
        out = parse(bytes(data))
        self.assertEqual((out["songs"], out["song_list"]), (1, []))


if __name__ == "__main__":
    unittest.main()
