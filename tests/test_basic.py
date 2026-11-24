import struct
import unittest

from cdtextkit.cdtext import crc16, parse

PACK = bytes([0x80, 1, 0, 0]) + b"Song\0".ljust(12, b"\0") + b"\0\0"


def pack(kind, track, seq, pos, text, block=0, double_byte=False):
    p = bytes([kind, track, seq, (block << 4) | pos | (0x80 if double_byte else 0)]) + text
    p = p.ljust(16, b"\0")
    return p + struct.pack(">H", (~crc16(p)) & 0xFFFF)


def packs(*ps):
    return b"".join(ps)


class BasicTests(unittest.TestCase):
    def test_count(self):
        self.assertEqual(parse(PACK * 2)["packs"], 2)

    def test_block(self):
        self.assertEqual(parse(PACK)["blocks"][0]["block"], 0)

    def test_crc(self):
        good = pack(0x80, 0, 0, 0, b"Title\0")
        self.assertEqual(parse(good)["crc_errors"], 0)
        self.assertEqual(parse(PACK)["crc_errors"], 1)

    def test_header(self):
        body = pack(0x80, 0, 0, 0, b"Title\0")
        self.assertEqual(parse(b"\x00\x14\x00\x00" + body)["packs"], 1)

    def test_string_spanning_packs(self):
        data = packs(pack(0x80, 0, 0, 0, b"Harbour Ligh"),
                     pack(0x80, 0, 1, 12, b"ts\0"))
        self.assertEqual(parse(data)["blocks"][0]["album"]["title"], "Harbour Lights")

    def test_track_numbers_within_pack(self):
        # One pack ends track 1's title and starts track 2's.
        data = packs(pack(0x80, 1, 0, 0, b"One\0Two\0"),
                     pack(0x80, 2, 1, 0, b""))
        tracks = parse(data)["blocks"][0]["tracks"]
        self.assertEqual(tracks, [{"number": 1, "title": "One"},
                                  {"number": 2, "title": "Two"}])

    def test_album_not_a_track(self):
        data = pack(0x80, 0, 0, 0, b"Album\0")
        block = parse(data)["blocks"][0]
        self.assertEqual(block["album"]["title"], "Album")
        self.assertEqual(block["tracks"], [])

    def test_upc_isrc(self):
        data = packs(pack(0x8E, 0, 0, 0, b"060254789654"),
                     pack(0x8E, 0, 1, 12, b"1\0GBUM715050"),
                     pack(0x8E, 1, 2, 10, b"78\0"))
        block = parse(data)["blocks"][0]
        self.assertEqual(block["album"]["upc"], "0602547896541")
        self.assertEqual(block["tracks"], [{"number": 1, "isrc": "GBUM71505078"}])

    def test_genre(self):
        data = pack(0x87, 0, 0, 0, b"\x00\x17Indie Rock")
        album = parse(data)["blocks"][0]["album"]
        self.assertEqual(album["genre"], 23)
        self.assertEqual(album["genre_text"], "Indie Rock")

    def test_size_info(self):
        data = packs(pack(0x8F, 0, 0, 0, b"\x00\x01\x0c"),
                     pack(0x8F, 0, 1, 0, b""),
                     pack(0x8F, 0, 2, 0, b"\0\0\0\0\x09"))
        block = parse(data)["blocks"][0]
        self.assertEqual(block["charset"], "iso-8859-1")
        self.assertEqual(block["language"], 9)

    def test_second_block(self):
        data = packs(pack(0x80, 0, 0, 0, b"One\0", block=0),
                     pack(0x80, 0, 1, 0, b"Zwei\0", block=1))
        blocks = parse(data)["blocks"]
        self.assertEqual([b["block"] for b in blocks], [0, 1])
        self.assertEqual(blocks[1]["album"]["title"], "Zwei")

    def test_double_byte(self):
        data = packs(pack(0x80, 0, 0, 0, "日本\0".encode("cp932"), double_byte=True),
                     pack(0x8F, 0, 1, 0, b"\x80"))
        block = parse(data)["blocks"][0]
        self.assertTrue(block["double_byte"])
        self.assertEqual(block["charset"], "ms-jis")
        self.assertEqual(block["album"]["title"], "日本")
