import struct
import unittest

from aykit.ay import parse


def be16(v):
    return struct.pack(">H", v & 0xFFFF)


def relptr(field_at, target_at):
    return be16((target_at - field_at) & 0xFFFF)


class Builder:
    """Assemble a minimal ZXAYEMUL-flavoured file from independent pieces."""

    def __init__(self, songs, first=0, author=None, misc=None):
        self.songs = songs
        self.first = first
        self.author = author
        self.misc = misc

    def build(self):
        blob = bytearray()

        def put(b):
            at = len(blob)
            blob.extend(b)
            return at

        strings = {}

        def string_at(text):
            if text is None:
                return None
            if text not in strings:
                strings[text] = put(text.encode("latin-1") + b"\0")
            return strings[text]

        author_at = string_at(self.author)
        misc_at = string_at(self.misc)
        name_at = [string_at(s.get("name")) for s in self.songs]

        # Song data records: 14 bytes each, pointer slots filled in later.
        song_at = []
        for s in self.songs:
            at = len(blob)
            song_at.append(at)
            ch = s.get("channels", (1, 2, 3, 7))
            blob.extend(bytes(ch))
            blob.extend(be16(s.get("length", 0)))
            blob.extend(be16(s.get("fade", 0)))
            blob.extend(b"\0" * 6)

        # Optional high/low register records.
        regs_at = []
        for s in self.songs:
            if "regs" in s:
                regs_at.append(put(be16(s["regs"][0]) + be16(s["regs"][1])))
            else:
                regs_at.append(None)

        # Player point records (stack, init, interrupt).
        points_at = []
        for s in self.songs:
            if s.get("points") is not None:
                stack, init, irq = s["points"]
                points_at.append(put(be16(stack) + be16(init) + be16(irq)))
            else:
                points_at.append(None)

        # Memory block tables, each terminated by an all-zero entry.
        blocks_at = []
        for si, s in enumerate(self.songs):
            if "blocks" not in s:
                blocks_at.append(None)
                continue
            at = len(blob)
            blocks_at.append(at)
            for addr, length in s.get("block_table", []):
                blob.extend(be16(addr) + be16(length) + b"\0\0")
            blob.extend(b"\0" * 6)

        # Block payload bytes live at the tail of the file, like real .ay files,
        # so a short payload is naturally truncated by the file boundary.
        payload_at = []
        for si, s in enumerate(self.songs):
            row = []
            for bi, payload in enumerate(s.get("blocks", [])):
                row.append(put(payload))
                table = blocks_at[si]
                if table is not None:
                    entry = table + 6 * bi
                    blob[entry + 4:entry + 6] = relptr(entry + 4, row[-1])
            payload_at.append(row)

        # Backfill the three relative pointers inside each song record.
        for i, at in enumerate(song_at):
            blob[at + 8:at + 10] = (relptr(at + 8, regs_at[i])
                                    if regs_at[i] is not None else b"\0\0")
            blob[at + 10:at + 12] = (relptr(at + 10, points_at[i])
                                     if points_at[i] is not None else b"\0\0")
            blob[at + 12:at + 14] = (relptr(at + 12, blocks_at[i])
                                     if blocks_at[i] is not None else b"\0\0")

        n = len(self.songs)
        header = bytearray(20)
        list_area = bytearray(4 * (n + 1))  # last entry is the terminator
        body_at = len(header) + len(list_area)
        header[0:8] = b"ZXAYEMUL"
        header[8] = 3
        header[9] = 1
        header[16] = n - 1  # zero-based index of the last song
        header[17] = self.first
        if author_at is not None:
            header[12:14] = relptr(12, body_at + author_at)
        if misc_at is not None:
            header[14:16] = relptr(14, body_at + misc_at)
        header[18:20] = relptr(18, len(header))  # song list follows the header
        for i in range(n):
            ent = len(header) + 4 * i
            if name_at[i] is not None:
                list_area[4 * i:4 * i + 2] = relptr(ent, body_at + name_at[i])
            list_area[4 * i + 2:4 * i + 4] = relptr(
                ent + 2, body_at + song_at[i])
        return bytes(header) + bytes(list_area) + bytes(blob)


class ParseTests(unittest.TestCase):
    def test_song_count_is_plus_one_and_strings(self):
        doc = parse(Builder(
            [{"name": "One"}, {"name": "Two"}, {"name": "Three"}],
            author="Ace", misc="notes here").build())
        self.assertEqual(doc["songs"], 3)
        self.assertEqual(doc["author"], "Ace")
        self.assertEqual(doc["misc"], "notes here")
        self.assertEqual([s["name"] for s in doc["song_list"]],
                         ["One", "Two", "Three"])

    def test_empty_header_strings_become_null(self):
        doc = parse(Builder([{"name": "Solo"}], author="", misc="").build())
        self.assertIsNone(doc["author"])
        self.assertIsNone(doc["misc"])
        self.assertEqual(doc["songs"], 1)

    def test_timing_uses_50hz_and_unknown_length(self):
        doc = parse(Builder([
            {"name": "a", "length": 125, "fade": 25},
            {"name": "b", "length": 0, "fade": 0},
        ]).build())
        self.assertEqual(doc["song_list"][0]["length_seconds"], "5/2")
        self.assertEqual(doc["song_list"][0]["fade_seconds"], "1/2")
        self.assertIsNone(doc["song_list"][1]["length_seconds"])
        self.assertIsNone(doc["song_list"][1]["fade_seconds"])

    def test_channels_and_regs_big_endian(self):
        doc = parse(Builder([{
            "name": "a", "channels": (2, 1, 0, 5),
            "regs": (0x1234, 0x00FF)}]).build())
        s = doc["song_list"][0]
        self.assertEqual(s["channels"], {"a": 2, "b": 1, "c": 0, "noise": 5})
        self.assertEqual(s["hi_reg"], 0x1234)
        self.assertEqual(s["lo_reg"], 0x00FF)

    def test_missing_regs_and_points_are_null(self):
        s = parse(Builder([{"name": "a"}]).build())["song_list"][0]
        self.assertIsNone(s["hi_reg"])
        self.assertIsNone(s["lo_reg"])
        self.assertIsNone(s["stack"])
        self.assertIsNone(s["init"])
        self.assertIsNone(s["interrupt"])

    def test_points_are_big_endian(self):
        s = parse(Builder([{
            "name": "a", "points": (0xFFF0, 0x8000, 0x8006)}]).build())[
            "song_list"][0]
        self.assertEqual(s["stack"], "$FFF0")
        self.assertEqual(s["init"], "$8000")
        self.assertEqual(s["interrupt"], "$8006")

    def test_blocks_capped_by_address_space_and_file(self):
        payload = bytes(range(256))
        s = parse(Builder([{
            "name": "a", "blocks": [payload],
            "block_table": [(0xFFF0, 0x1000)]}]).build())["song_list"][0]
        self.assertEqual(s["blocks"], 1)
        self.assertEqual(s["block_bytes"], 16)  # 0x10000 - 0xFFF0
        s2 = parse(Builder([{
            "name": "a", "blocks": [payload],
            "block_table": [(0x8000, 0x1000)]}]).build())["song_list"][0]
        self.assertEqual(s2["block_bytes"], 256)  # only 256 bytes in the file

    def test_shared_code_block_counted_per_song(self):
        payload = b"\xc9" * 64
        doc = parse(Builder([
            {"name": "one", "blocks": [payload], "block_table": [(0x8000, 64)]},
            {"name": "two", "blocks": [payload], "block_table": [(0x8000, 64)]},
        ]).build())
        self.assertEqual(doc["songs"], 2)
        self.assertEqual([s["blocks"] for s in doc["song_list"]], [1, 1])
        self.assertEqual([s["block_bytes"] for s in doc["song_list"]], [64, 64])

    def test_song_without_name_keeps_its_data(self):
        s = parse(Builder([{
            "name": None, "length": 3000, "fade": 100,
            "channels": (0, 1, 2, 3),
            "points": (0xF000, 0x8000, 0x8003),
            "blocks": [b"\0" * 10], "block_table": [(0x9000, 10)]}]).build())[
            "song_list"][0]
        self.assertIsNone(s["name"])
        self.assertEqual(s["length_seconds"], "60")
        self.assertEqual(s["fade_seconds"], "2")
        self.assertEqual(s["init"], "$8000")
        self.assertEqual(s["block_bytes"], 10)

    def test_first_song_passthrough(self):
        doc = parse(Builder(
            [{"name": "a"}, {"name": "b"}], first=1).build())
        self.assertEqual(doc["first_song"], 1)

    def test_truncated_file_keeps_what_is_available(self):
        full = Builder([{
            "name": "a", "points": (0x1234, 0x5678, 0x9ABC),
            "blocks": [b"\xaa" * 32], "block_table": [(0x8000, 32)]}]).build()
        cut = full[:len(full) - 10]  # chop the end of the block payload
        s = parse(cut)["song_list"][0]
        self.assertEqual(s["blocks"], 1)
        self.assertEqual(s["block_bytes"], 22)  # 32 declared, 22 remain
        self.assertEqual(s["init"], "$5678")  # points record is still intact

    def test_relative_pointer_past_end_of_file_is_ignored(self):
        data = bytearray(Builder([{"name": "a"}]).build())
        # Corrupt the points pointer so its target lies beyond the file.
        # Song record starts right after the header and one list entry.
        song = 20 + 8
        data[song + 10:song + 12] = b"\x7f\xff"
        s = parse(bytes(data))["song_list"][0]
        self.assertIsNone(s["stack"])
        self.assertIsNone(s["init"])
        self.assertIsNone(s["interrupt"])

    def test_block_running_into_end_of_memory(self):
        payload = b"\x00" * 100
        s = parse(Builder([{
            "name": "a", "blocks": [payload],
            "block_table": [(0xFFFF, 100)]}]).build())["song_list"][0]
        self.assertEqual(s["block_bytes"], 1)  # only address 0xFFFF exists

    def test_block_table_pointer_missing(self):
        doc = parse(Builder([{"name": "a"}]).build())
        self.assertEqual(doc["song_list"][0]["blocks"], 0)
        self.assertEqual(doc["song_list"][0]["block_bytes"], 0)
