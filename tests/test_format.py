"""Format details: song counts, timing units, addresses, block truncation and missing player points."""
import struct
import unittest

from aykit.ay import AyError, parse

HEADER_SIZE = 20


class AyFile:
    """A ZXAYEMUL file built up in memory; relative pointers are patched once the layout is known."""

    def __init__(self, songs):
        self.buf = bytearray(HEADER_SIZE)
        self.buf[:8] = b"ZXAYEMUL"
        self.buf[8] = 3
        self.buf[9] = 1
        self.buf[16] = songs - 1
        self.buf[17] = 0
        self.list_at = self.alloc(4 * songs)
        self.set_rel(18, self.list_at)
        self.entries = [self.list_at + 4 * i for i in range(songs)]

    def alloc(self, size):
        at = len(self.buf)
        self.buf += bytes(size)
        return at

    def set_rel(self, at, target):
        struct.pack_into(">h", self.buf, at, target - at)

    def add_string(self, text):
        at = self.alloc(len(text) + 1)
        self.buf[at:at + len(text)] = text.encode("latin-1")
        return at

    def name_song(self, index, text):
        self.set_rel(self.entries[index], self.add_string(text))

    def add_song_data(self, channels=(0, 1, 2, 3), length=100, fade=5, hi=0, lo=0,
                      points=None, blocks=None):
        at = self.alloc(14)
        self.buf[at:at + 4] = bytes(channels)
        struct.pack_into(">HH", self.buf, at + 4, length, fade)
        self.buf[at + 8] = hi
        self.buf[at + 9] = lo
        if points is not None:
            self.set_rel(at + 10, points)
        if blocks is not None:
            self.set_rel(at + 12, blocks)
        return at

    def attach(self, index, song_data):
        self.set_rel(self.entries[index] + 2, song_data)

    def add_points(self, stack=0xFFF0, init=0x8000, interrupt=0x8006):
        at = self.alloc(6)
        struct.pack_into(">HHH", self.buf, at, stack, init, interrupt)
        return at

    def add_block_table(self, blocks, data=b""):
        """`blocks` are (load address, declared length) pairs; every block points at `data`."""
        table_at = self.alloc(6 * (len(blocks) + 1))
        data_at = self.alloc(len(data))
        self.buf[data_at:data_at + len(data)] = data
        for i, (load_addr, length) in enumerate(blocks):
            p = table_at + 6 * i
            struct.pack_into(">HH", self.buf, p, load_addr, length)
            self.set_rel(p + 4, data_at)
        return table_at

    def add_block_table_at(self, blocks, addresses):
        """Like `add_block_table`, but each block gets its own data pointer."""
        table_at = self.alloc(6 * (len(blocks) + 1))
        for i, ((load_addr, length), target) in enumerate(zip(blocks, addresses)):
            p = table_at + 6 * i
            struct.pack_into(">HH", self.buf, p, load_addr, length)
            self.set_rel(p + 4, target)
        return table_at

    def parse(self):
        return parse(bytes(self.buf))


def simple_file(songs=1, **song_data):
    """One file with `songs` song entries and no player points."""
    f = AyFile(songs)
    for i in range(songs):
        f.attach(i, f.add_song_data(**song_data))
    return f


class SongCountTests(unittest.TestCase):
    def test_count_is_one_more_than_stored(self):
        self.assertEqual(simple_file(1).parse()["songs"], 1)
        self.assertEqual(simple_file(3).parse()["songs"], 3)

    def test_song_list_has_every_song(self):
        out = simple_file(3).parse()
        self.assertEqual(len(out["song_list"]), 3)

    def test_first_song_is_stored_verbatim(self):
        f = simple_file(4)
        f.buf[17] = 2
        self.assertEqual(f.parse()["first_song"], 2)

    def test_missing_song_list_pointer_gives_no_songs(self):
        out = parse(b"ZXAYEMUL" + bytes([3, 1]) + bytes(10))
        self.assertEqual(out["songs"], 1)
        self.assertEqual(out["song_list"], [])


class TimingTests(unittest.TestCase):
    def test_length_is_in_fiftieths_of_a_second(self):
        f = simple_file(length=150, fade=0)
        self.assertEqual(f.parse()["song_list"][0]["length_seconds"], "3")

    def test_fade_is_in_fiftieths_of_a_second(self):
        f = simple_file(length=100, fade=25)
        self.assertEqual(f.parse()["song_list"][0]["fade_seconds"], "1/2")

    def test_zero_length_is_unknown(self):
        f = simple_file(length=0, fade=7)
        entry = f.parse()["song_list"][0]
        self.assertIsNone(entry["length_seconds"])
        self.assertEqual(entry["fade_seconds"], "7/50")


class SongDataTests(unittest.TestCase):
    def test_channel_mapping_and_register_values(self):
        f = simple_file(channels=(2, 1, 0, 3), hi=0x0F, lo=0x07)
        entry = f.parse()["song_list"][0]
        self.assertEqual(entry["channels"], [2, 1, 0, 3])
        self.assertEqual((entry["hi_reg"], entry["lo_reg"]), (0x0F, 0x07))

    def test_name_author_and_misc(self):
        f = simple_file()
        f.set_rel(12, f.add_string("Rob Hubbard"))
        f.set_rel(14, f.add_string("Notes"))
        f.name_song(0, "Monty on the Run")
        out = f.parse()
        self.assertEqual((out["author"], out["misc"]), ("Rob Hubbard", "Notes"))
        self.assertEqual(out["song_list"][0]["name"], "Monty on the Run")

    def test_absent_strings_are_null(self):
        out = simple_file().parse()
        self.assertIsNone(out["author"])
        self.assertIsNone(out["misc"])
        self.assertIsNone(out["song_list"][0]["name"])


class PlayerPointTests(unittest.TestCase):
    def song_data_at(self, f):
        return f.list_at + 4

    def test_addresses_are_big_endian(self):
        f = simple_file()
        player = f.add_points(0x1234, 0x5678, 0x9ABC)
        f.set_rel(self.song_data_at(f) + 10, player)
        entry = f.parse()["song_list"][0]
        self.assertEqual((entry["stack"], entry["init"], entry["interrupt"]),
                         ("$1234", "$5678", "$9ABC"))

    def test_init_falls_back_to_interrupt(self):
        f = simple_file()
        player = f.add_points(0xFFF0, 0x0000, 0x8006)
        f.set_rel(self.song_data_at(f) + 10, player)
        entry = f.parse()["song_list"][0]
        self.assertEqual((entry["init"], entry["interrupt"]), ("$8006", "$8006"))

    def test_no_points_gives_nulls(self):
        entry = simple_file(length=50).parse()["song_list"][0]
        self.assertEqual((entry["stack"], entry["init"], entry["interrupt"]), (None, None, None))
        self.assertEqual(entry["channels"], [0, 1, 2, 3])
        self.assertEqual(entry["length_seconds"], "1")


class BlockTests(unittest.TestCase):
    def attach_table(self, f, table):
        f.set_rel(f.list_at + 4 + 12, table)

    def test_block_capped_by_z80_address_space(self):
        f = simple_file()
        self.attach_table(f, f.add_block_table([(0xF000, 0x1F40)], b"\xC9" * 0x1F40))
        entry = f.parse()["song_list"][0]
        self.assertEqual((entry["blocks"], entry["block_bytes"]), (1, 0x1000))

    def test_block_capped_by_file_length(self):
        f = simple_file()
        self.attach_table(f, f.add_block_table([(0x8000, 0x2000)], b"\xC9" * 0x40))
        entry = f.parse()["song_list"][0]
        self.assertEqual(entry["block_bytes"], 0x40)

    def test_blocks_add_up(self):
        f = simple_file()
        self.attach_table(f, f.add_block_table([(0x8000, 0x100), (0xFFF0, 0x100)], b"\xC9" * 0x200))
        entry = f.parse()["song_list"][0]
        self.assertEqual((entry["blocks"], entry["block_bytes"]), (2, 0x100 + 0x10))

    def test_block_pointer_past_end_loads_nothing(self):
        f = simple_file()
        self.attach_table(f, f.add_block_table_at([(0x8000, 0x100)], [len(f.buf) + 0x1000]))
        entry = f.parse()["song_list"][0]
        self.assertEqual((entry["blocks"], entry["block_bytes"]), (0, 0))

    def test_two_songs_share_one_code_block(self):
        f = AyFile(2)
        shared = f.add_block_table([(0x8000, 0x40)], b"\xC9" * 0x40)
        for i in range(2):
            f.attach(i, f.add_song_data(length=50, blocks=shared))
        out = f.parse()
        self.assertEqual([e["blocks"] for e in out["song_list"]], [1, 1])
        self.assertEqual([e["block_bytes"] for e in out["song_list"]], [0x40, 0x40])

    def test_song_without_points_still_loads_blocks(self):
        f = simple_file()
        self.attach_table(f, f.add_block_table([(0x8000, 0x40)], b"\xC9" * 0x40))
        entry = f.parse()["song_list"][0]
        self.assertIsNone(entry["init"])
        self.assertEqual(entry["block_bytes"], 0x40)


class RobustnessTests(unittest.TestCase):
    def test_every_truncation_of_an_example_parses(self):
        with open("examples/compilation.ay", "rb") as fh:
            data = fh.read()
        for size in range(len(data) + 1):
            try:
                parse(data[:size])
            except AyError:
                # Only a prefix too short to hold the header may be rejected.
                self.assertLess(size, HEADER_SIZE)
            except Exception as exc:  # noqa: BLE001 - anything else is a parser bug
                self.fail("prefix of %d bytes raised %r" % (size, exc))

    def test_header_only_file(self):
        out = parse(b"ZXAYEMUL" + bytes([3, 1]) + bytes(10))
        self.assertEqual((out["songs"], out["song_list"]), (1, []))

    def test_full_song_count(self):
        data = bytearray(b"ZXAYEMUL" + bytes([3, 1]) + bytes(10))
        data[16] = 255
        self.assertEqual(parse(bytes(data))["songs"], 256)


class ExampleTests(unittest.TestCase):
    def load(self, name):
        with open("examples/" + name, "rb") as fh:
            return parse(fh.read())

    def test_agent_x(self):
        out = self.load("agent_x.ay")
        self.assertEqual((out["author"], out["misc"]), ("Tim Follin", "Converted with AYMakeR"))
        self.assertEqual(out["songs"], 1)
        entry = out["song_list"][0]
        self.assertEqual(entry["name"], "Agent X II - Level 1")
        self.assertEqual(entry["channels"], [0, 1, 2, 3])
        self.assertEqual((entry["stack"], entry["init"], entry["interrupt"]),
                         ("$FFF0", "$8000", "$8006"))
        self.assertEqual((entry["blocks"], entry["block_bytes"]), (2, 2500))
        self.assertEqual(entry["length_seconds"], "240")

    def test_compilation(self):
        out = self.load("compilation.ay")
        self.assertEqual(out["author"], "Various")
        self.assertEqual((out["songs"], out["first_song"]), (3, 1))
        self.assertEqual([e["name"] for e in out["song_list"]], ["Title", "In Game", None])

    def test_compilation_blocks(self):
        songs = self.load("compilation.ay")["song_list"]
        # First song: 0xF000 + 0x1F40 would run past 64K, so only 0x1000 bytes load.
        self.assertEqual((songs[0]["blocks"], songs[0]["block_bytes"]), (1, 0x1000))
        # The later songs reuse the code block area and load 0x40 bytes each.
        self.assertEqual([(s["blocks"], s["block_bytes"]) for s in songs[1:]], [(1, 0x40)] * 2)

    def test_compilation_last_song_has_no_points(self):
        last = self.load("compilation.ay")["song_list"][2]
        self.assertEqual((last["stack"], last["init"], last["interrupt"]), (None, None, None))
        self.assertEqual(last["length_seconds"], "180")


if __name__ == "__main__":
    unittest.main()
