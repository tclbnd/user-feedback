import struct
import unittest

from dlskit.dls import parse

from banks import ck, instrument, make_bank, region, wave, wsmp


def one(inst_bytes, waves, cues=None):
    out = parse(make_bank([inst_bytes], waves, cues))
    return out["instruments"][0]


class BankSelectTests(unittest.TestCase):
    """ulBank holds the LSB in its first byte and the 7-bit MSB in its second."""

    def test_msb_and_lsb_are_split(self):
        inst = one(instrument(bank=(8 << 7) | 3, program=48), [wave()])
        self.assertEqual((inst["bank_msb"], inst["bank_lsb"]), (8, 3))

    def test_zero_bank_is_not_assumed(self):
        inst = one(instrument(bank=(121 << 7) | 1), [wave()])
        self.assertEqual((inst["bank_msb"], inst["bank_lsb"]), (121, 1))

    def test_msb_is_seven_bits(self):
        inst = one(instrument(bank=127 << 7), [wave()])
        self.assertEqual(inst["bank_msb"], 127)


class DrumTests(unittest.TestCase):
    def test_drum_flag_is_reported(self):
        inst = one(instrument(bank=0, program=0, drum=True), [wave()])
        self.assertTrue(inst["drums"])

    def test_drum_flag_does_not_leak_into_bank(self):
        inst = one(instrument(bank=(120 << 7) | 2, drum=True), [wave()])
        self.assertEqual((inst["bank_msb"], inst["bank_lsb"], inst["program"]), (120, 2, 0))

    def test_melodic_instrument_is_not_a_drum(self):
        inst = one(instrument(bank=(120 << 7) | 2), [wave()])
        self.assertFalse(inst["drums"])


class Level2RegionTests(unittest.TestCase):
    """DLS2 tools write region lists as `rgn2` rather than `rgn `."""

    def test_rgn2_regions_are_read(self):
        inst = one(instrument(regions=[region(keys=(40, 45), kind=b"rgn2")]), [wave()])
        self.assertEqual(len(inst["regions"]), 1)
        self.assertEqual(inst["regions"][0]["keys"], [40, 45])

    def test_level1_and_level2_regions_can_mix(self):
        inst = one(instrument(regions=[region(keys=(0, 30), kind=b"rgn "),
                                       region(keys=(31, 127), kind=b"rgn2")]),
                   [wave(), wave()])
        self.assertEqual([r["keys"] for r in inst["regions"]], [[0, 30], [31, 127]])


class OddNameTests(unittest.TestCase):
    """A chunk with an odd-sized body is followed by a pad byte."""

    def test_instruments_after_an_odd_name_survive(self):
        out = parse(make_bank([instrument(name=b"Odd", program=1, regions=[region()]),
                               instrument(name=b"Even", program=2, regions=[region()])],
                              [wave()]))
        self.assertEqual([i["name"] for i in out["instruments"]], ["Odd", "Even"])
        self.assertEqual([i["program"] for i in out["instruments"]], [1, 2])

    def test_waves_after_an_odd_name_survive(self):
        out = parse(make_bank([instrument(regions=[region()])],
                              [wave(frames=10, name=b"odd"),
                               wave(frames=20, name=b"even"),
                               wave(frames=30)]))
        self.assertEqual(out["waves"], 3)
        self.assertEqual(out["wave_frames"], 60)

    def test_odd_name_before_later_waves_and_regions(self):
        out = parse(make_bank([instrument(name=b"X", regions=[region(cue=1)]),
                               instrument(name=b"Yy", regions=[region(cue=2)])],
                              [wave(frames=10), wave(frames=20), wave(frames=30)]))
        self.assertEqual([i["name"] for i in out["instruments"]], ["X", "Yy"])
        self.assertEqual([i["regions"][0]["wave"] for i in out["instruments"]], [1, 2])

    def test_sibling_fields_after_an_odd_name_survive(self):
        # as DLS2 tools write it: an odd INAM followed by more INFO fields
        extra = ck(b"ISFT", b"tool")
        inst = one(instrument(name=b"Kit", regions=[region()], extra_info=extra),
                   [wave(name=b"snr", extra_info=extra)])
        self.assertEqual(inst["name"], "Kit")
        self.assertEqual(inst["regions"][0]["keys"], [0, 127])

    def test_waves_after_an_odd_data_chunk_survive(self):
        out = parse(make_bank([instrument(regions=[region(cue=2)])],
                              [wave(data_size=8000, name=b"snr"),
                               wave(data_size=12003, name=b"kick"),
                               wave(data_size=6000)]))
        self.assertEqual(out["waves"], 3)
        self.assertEqual(out["instruments"][0]["regions"][0]["wave"], 2)


class WaveLinkTests(unittest.TestCase):
    """ulTableIndex indexes the ptbl cue table, not the wave pool directly."""

    def test_cue_table_is_followed(self):
        # cue 0 points at the third wave, cue 1 at the first
        inst = one(instrument(regions=[region(cue=0), region(cue=1)]),
                   [wave(frames=10), wave(frames=20), wave(frames=30)],
                   cues=[2, 0])
        self.assertEqual([r["wave"] for r in inst["regions"]], [2, 0])

    def test_unresolvable_cue_is_null(self):
        inst = one(instrument(regions=[region(cue=9)]), [wave()])
        self.assertIsNone(inst["regions"][0]["wave"])


class SampleInfoTests(unittest.TestCase):
    """Sample information comes from the region's wsmp, else the wave's."""

    def test_region_wsmp_wins_over_the_wave(self):
        inst = one(instrument(regions=[region(wsmp_body=wsmp(unity=72, loop=(5, 50)))]),
                   [wave(wsmp_body=wsmp(unity=60))])
        reg = inst["regions"][0]
        self.assertEqual(reg["unity_note"], 72)
        self.assertEqual(reg["loop"], {"type": "forward", "start": 5, "length": 50})
        self.assertTrue(reg["looped"])

    def test_wave_wsmp_applies_when_the_region_has_none(self):
        inst = one(instrument(regions=[region()]),
                   [wave(wsmp_body=wsmp(unity=64, gain=6 * 65536))])
        reg = inst["regions"][0]
        self.assertEqual(reg["unity_note"], 64)
        self.assertEqual(reg["gain_db"], "6.00")

    def test_no_sample_info_anywhere(self):
        inst = one(instrument(regions=[region()]), [wave()])
        reg = inst["regions"][0]
        self.assertIsNone(reg["unity_note"])
        self.assertIsNone(reg["gain_db"])
        self.assertIsNone(reg["loop"])
        self.assertFalse(reg["looped"])

    def test_wave_wsmp_follows_the_cue_to_the_right_wave(self):
        inst = one(instrument(regions=[region(cue=0)]),
                   [wave(wsmp_body=wsmp(unity=50)), wave(wsmp_body=wsmp(unity=70))],
                   cues=[1])
        self.assertEqual(inst["regions"][0]["unity_note"], 70)


class PaddingTests(unittest.TestCase):
    """Top-level chunks are padded too."""

    def test_odd_chunk_at_top_level(self):
        ptbl = ck(b"ptbl", struct.pack("<III", 8, 1, 0))
        body = (b"DLS " + ck(b"colh", b"\0\0\1")
                + ck(b"LIST", b"lins" + instrument(name=b"Z", regions=[region()]))
                + ptbl + ck(b"LIST", b"wvpl" + wave(frames=10)))
        out = parse(b"RIFF" + struct.pack("<I", len(body)) + body)
        self.assertEqual(out["instruments"][0]["name"], "Z")
        self.assertEqual(out["waves"], 1)


if __name__ == "__main__":
    unittest.main()
