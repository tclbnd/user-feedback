import unittest

from dlskit.dls import parse

from banks import bank, build, instrument, region, wave


class BasicTests(unittest.TestCase):
    def test_waves(self):
        out = parse(bank(250))
        self.assertEqual((out["waves"], out["wave_frames"]), (1, 250))

    def test_instrument(self):
        inst = parse(bank())["instruments"][0]
        self.assertEqual((inst["program"], len(inst["regions"]), inst["regions"][0]["keys"]), (5, 1, [0, 127]))


class BankAndDrumTests(unittest.TestCase):
    def test_bank_msb_lsb_split(self):
        # Strings living outside bank 0 must keep both bank select bytes.
        bank_field = (3 << 16) | (5 << 24)
        ins = instrument(bank=bank_field, program=48, regions=[region(link=0)])
        inst = parse(build([ins], [wave()]))["instruments"][0]
        self.assertEqual((inst["bank_msb"], inst["bank_lsb"], inst["program"]), (3, 5, 48))
        self.assertFalse(inst["drums"])

    def test_drum_kit_flag(self):
        drums = instrument(bank=1 << 31, program=0, regions=[region(link=0)])
        melodic = instrument(bank=0, program=33, regions=[region(link=0)])
        out = parse(build([drums, melodic], [wave(), wave()]))
        self.assertTrue(out["instruments"][0]["drums"])
        self.assertFalse(out["instruments"][1]["drums"])
        # The drum flag must not leak into the bank select bytes.
        self.assertEqual((out["instruments"][0]["bank_msb"], out["instruments"][0]["bank_lsb"]), (0, 0))


class Dls2Tests(unittest.TestCase):
    def test_rgn2_regions_are_read(self):
        regions = [region(35, 36, link=0, rgn2=True), region(38, 40, link=1, rgn2=True)]
        ins = instrument(program=0, regions=regions)
        out = parse(build([ins], [wave(), wave()]))["instruments"][0]
        self.assertEqual([r["keys"] for r in out["regions"]], [[35, 36], [38, 40]])
        self.assertEqual([r["wave"] for r in out["regions"]], [0, 1])


class PaddingTests(unittest.TestCase):
    def test_odd_name_does_not_drop_following_chunks(self):
        first = instrument(program=1, regions=[region(link=0)], name="X")       # odd INAM
        second = instrument(program=2, regions=[region(link=1)], name="HELLO")  # odd INAM
        out = parse(build([first, second], [wave(name="a"), wave(name="bbbbb")],
                          bank_name="ODD"))
        self.assertEqual([i["name"] for i in out["instruments"]], ["X", "HELLO"])
        self.assertEqual(out["waves"], 2)
        self.assertEqual(out["name"], "ODD")
        # The second instrument's region and wave still resolve after the pad.
        self.assertEqual(out["instruments"][1]["regions"][0]["wave"], 1)


class WaveLinkTests(unittest.TestCase):
    def test_region_resolves_wave_through_pool_table(self):
        regions = [region(link=i) for i in range(3)]
        ins = instrument(regions=regions)
        out = parse(build([ins], [wave(), wave(), wave()]))["instruments"][0]
        self.assertEqual([r["wave"] for r in out["regions"]], [0, 1, 2])

    def test_sample_info_falls_back_to_wave_wsmp(self):
        w = wave(with_wsmp={"unity": 72, "gain": 65536, "loops": 1,
                            "start": 50, "length": 200})
        out = parse(build([instrument(regions=[region(link=0)])], [w]))
        reg = out["instruments"][0]["regions"][0]
        self.assertEqual((reg["unity_note"], reg["gain_db"], reg["looped"]), (72, "1.00", True))
        self.assertEqual(reg["loop"], {"type": "forward", "start": 50, "length": 200})

    def test_region_wsmp_overrides_wave_wsmp(self):
        w = wave(with_wsmp={"unity": 72, "loops": 1, "start": 50, "length": 200})
        r = region(link=0, with_wsmp={"unity": 48, "loops": 0})
        reg = parse(build([instrument(regions=[r])], [w]))["instruments"][0]["regions"][0]
        self.assertEqual(reg["unity_note"], 48)
        self.assertFalse(reg["looped"])
        self.assertIsNone(reg["loop"])

    def test_no_sample_info_anywhere(self):
        reg = parse(build([instrument(regions=[region(link=0)])], [wave()]))
        reg = reg["instruments"][0]["regions"][0]
        self.assertIsNone(reg["unity_note"])
        self.assertIsNone(reg["gain_db"])
        self.assertIsNone(reg["loop"])
        self.assertFalse(reg["looped"])
