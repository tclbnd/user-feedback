import struct
import unittest

from dlskit.dls import parse

from banks import bank, ck, rich_bank


class FixTests(unittest.TestCase):
    def setUp(self):
        self.out = parse(rich_bank())

    # 1. bank number split into MSB/LSB, non-zero banks must not read as zero
    def test_bank_split(self):
        drum, melodic = self.out["instruments"]
        self.assertEqual((drum["bank_msb"], drum["bank_lsb"]), (120, 1))
        self.assertEqual((melodic["bank_msb"], melodic["bank_lsb"]), (8, 5))
        self.assertEqual(melodic["program"], 33)

    # 2. drum kits are flagged, ordinary instruments are not
    def test_drums_flag(self):
        drum, melodic = self.out["instruments"]
        self.assertTrue(drum["drums"])
        self.assertFalse(melodic["drums"])

    # 3. DLS2 rgn2 regions parse just like DLS1 rgn regions
    def test_dls2_regions(self):
        drum, melodic = self.out["instruments"]
        self.assertEqual(len(drum["regions"]), 2)
        self.assertEqual(drum["regions"][0]["keys"], [35, 36])
        self.assertEqual(drum["regions"][1]["keys"], [38, 40])
        self.assertEqual(melodic["regions"][0]["keys"], [0, 127])

    # 4. odd-sized chunks: later instruments and waves are not lost
    def test_odd_chunk_padding(self):
        drum, melodic = self.out["instruments"]
        self.assertEqual([i["name"] for i in self.out["instruments"]], ["Kit", "Strings"])
        self.assertEqual(self.out["waves"], 2)
        # first wave held 3 bytes (1 frame at block alignment 2); second held 20 bytes
        self.assertEqual(self.out["wave_frames"], 11)

    # 5. region -> pool table -> wave pool index, and wave-level wsmp fallback
    def test_wave_resolution_and_sample_info(self):
        drum, melodic = self.out["instruments"]
        # shuffled table: index 0 -> wave 1, index 1 -> wave 0
        self.assertEqual(drum["regions"][0]["wave"], 0)   # table index 1
        self.assertEqual(drum["regions"][1]["wave"], 1)   # table index 0
        self.assertEqual(melodic["regions"][0]["wave"], 0)
        # region with its own wsmp keeps its parameters
        self.assertEqual(drum["regions"][0]["unity_note"], 60)
        # region pointing at a wave without a wsmp gets null sample parameters
        no_wsmp = drum["regions"][1]
        self.assertIsNone(no_wsmp["unity_note"])
        self.assertIsNone(no_wsmp["gain_db"])
        self.assertIsNone(no_wsmp["loop"])
        self.assertFalse(no_wsmp["looped"])
        # region without a wsmp inherits the one attached to the wave it plays
        # (wave 0 carries unity 48, gain 0.5 dB)
        inherited = melodic["regions"][0]
        self.assertEqual(inherited["unity_note"], 48)
        self.assertEqual(inherited["gain_db"], "0.50")
        self.assertIsNone(inherited["loop"])
        self.assertFalse(inherited["looped"])

    def test_unlinked_region_still_parses(self):
        # the minimal bank resolves its one region through the pool table
        reg = parse(bank())["instruments"][0]["regions"][0]
        self.assertEqual(reg["wave"], 0)
        self.assertEqual(reg["keys"], [0, 127])

    def test_unresolvable_link_is_null(self):
        # pool-table index 9 with a one-entry table: wave stays null and the
        # region still parses with null sample parameters
        fmt = struct.pack("<HHIIHH", 1, 1, 22050, 44100, 2, 16)
        wave = ck(b"LIST", b"wave" + ck(b"fmt ", fmt) + ck(b"data", bytes(20)))
        rgn = (ck(b"rgnh", struct.pack("<6H", 0, 127, 0, 127, 0, 0))
               + ck(b"wlnk", struct.pack("<HHII", 0, 0, 1, 9)))
        ins = (ck(b"insh", struct.pack("<III", 1, 0, 5))
               + ck(b"LIST", b"lrgn" + ck(b"LIST", b"rgn " + rgn)))
        body = (b"DLS " + ck(b"colh", struct.pack("<I", 1))
                + ck(b"LIST", b"lins" + ck(b"LIST", b"ins " + ins))
                + ck(b"ptbl", struct.pack("<III", 8, 1, 0))
                + ck(b"LIST", b"wvpl" + wave))
        out = parse(b"RIFF" + struct.pack("<I", len(body)) + body)
        reg = out["instruments"][0]["regions"][0]
        self.assertIsNone(reg["wave"])
        self.assertIsNone(reg["unity_note"])
        self.assertIsNone(reg["gain_db"])
        self.assertIsNone(reg["loop"])


if __name__ == "__main__":
    unittest.main()
