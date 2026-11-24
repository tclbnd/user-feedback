"""Coverage for the five DLS Level 1 / Level 2 audit corrections."""
import unittest

from dlskit.dls import parse

import dlsbuild as B


class BankSelectTests(unittest.TestCase):
    def test_msb_and_lsb_split(self):
        # MSB in bits 8-15, LSB in bits 0-7: a strings and a bass in bank 121.1
        strings = B.instrument(121 << 8 | 1, 48, [B.region(0, 127, 0)])
        bass = B.instrument(121 << 8 | 1, 33, [B.region(0, 127, 0)])
        bank = B.build([strings, bass], [B.wave(100)])
        out = parse(bank)
        got = [(i["bank_msb"], i["bank_lsb"], i["program"]) for i in out["instruments"]]
        self.assertEqual(got, [(121, 1, 48), (121, 1, 33)])

    def test_bank_zero_stays_zero(self):
        inst = B.instrument(0, 5, [B.region(0, 127, 0)])
        out = parse(B.build([inst], [B.wave(100)]))["instruments"][0]
        self.assertEqual((out["bank_msb"], out["bank_lsb"]), (0, 0))


class DrumKitTests(unittest.TestCase):
    def test_drum_flag_distinguishes_kit_from_melodic(self):
        kit = B.instrument(0, 0, [B.region(35, 36, 0)], name="Kit", drums=True)
        mel = B.instrument(0, 0, [B.region(0, 127, 1)], name="Piano")
        out = parse(B.build([kit, mel], [B.wave(50), B.wave(60)]))
        self.assertEqual([i["drums"] for i in out["instruments"]], [True, False])


class Dls2RegionTests(unittest.TestCase):
    def test_rgn2_regions_are_read(self):
        regs = [B.region(35, 36, 0, level2=True),
                B.region(38, 40, 1, level2=True),
                B.region(42, 46, 2, level2=True,
                         own_wsmp=B.wsmp(loops=1, start=5, length=9))]
        inst = B.instrument(0, 0, regs, drums=True)
        out = parse(B.build([inst], [B.wave(10), B.wave(20), B.wave(30)],
                            version=(2, 0, 0, 0)))
        parsed = out["instruments"][0]
        self.assertEqual(len(parsed["regions"]), 3)
        self.assertEqual([r["keys"] for r in parsed["regions"]],
                         [[35, 36], [38, 40], [42, 46]])
        self.assertTrue(parsed["regions"][2]["looped"])
        self.assertEqual(parsed["regions"][2]["loop"],
                         {"type": "forward", "start": 5, "length": 9})


class OddNamePaddingTests(unittest.TestCase):
    def test_odd_length_inam_keeps_later_chunks(self):
        # "Bass" + NUL is 5 bytes: an odd INAM inside an odd INFO list;
        # a second instrument and a named wave follow it.
        first = B.instrument(0, 33, [B.region(0, 127, 0)], name="Bass")
        second = B.instrument(0, 40, [B.region(0, 127, 1)], name="Violin")
        waves = [B.wave(100, name="snr"), B.wave(200)]
        out = parse(B.build([first, second], waves))
        names = [i["name"] for i in out["instruments"]]
        self.assertEqual(names, ["Bass", "Violin"])
        self.assertEqual(out["waves"], 2)
        self.assertEqual(out["wave_frames"], 300)

    def test_odd_wave_name_keeps_later_waves(self):
        waves = [B.wave(11, name="kick"), B.wave(22), B.wave(33)]
        inst = B.instrument(0, 1, [B.region(0, 127, 2)])
        out = parse(B.build([inst], waves))
        self.assertEqual(out["waves"], 3)
        self.assertEqual(out["wave_frames"], 66)
        # region still resolves through the pool table to the last wave
        self.assertEqual(out["instruments"][0]["regions"][0]["wave"], 2)


class WaveLinkTests(unittest.TestCase):
    def test_region_resolves_via_pool_table(self):
        waves = [B.wave(10), B.wave(20), B.wave(30), B.wave(40)]
        # table entries are shuffled relative to pool order
        order = [2, 0, 3, 1]
        regs = [B.region(0, 20, 0), B.region(21, 40, 1),
                B.region(41, 80, 2), B.region(81, 127, 3)]
        inst = B.instrument(0, 5, regs)
        out = parse(B.build([inst], waves, cue_order=order))
        self.assertEqual([r["wave"] for r in out["instruments"][0]["regions"]],
                         order)

    def test_wave_level_wsmp_applies_to_region_without_own(self):
        waves = [B.wave(40, with_wsmp=True), B.wave(50)]
        no_own = B.region(0, 63, 0)
        own = B.region(64, 127, 1, own_wsmp=B.wsmp(unity=48, gain=65536))
        inst = B.instrument(0, 5, [no_own, own])
        out = parse(B.build([inst], waves))
        regs = out["instruments"][0]["regions"]
        # falls back to the wave's own sample information
        self.assertEqual(regs[0]["unity_note"], 72)
        self.assertTrue(regs[0]["looped"])
        self.assertEqual(regs[0]["loop"],
                         {"type": "forward", "start": 10, "length": 20})
        # a region-level wsmp overrides the wave-level one
        self.assertEqual(regs[1]["unity_note"], 48)
        self.assertEqual(regs[1]["gain_db"], "1.00")
        self.assertFalse(regs[1]["looped"])

    def test_unresolvable_link_is_null(self):
        inst = B.instrument(0, 5, [B.region(0, 127, 9)])
        out = parse(B.build([inst], [B.wave(10)]))
        self.assertIsNone(out["instruments"][0]["regions"][0]["wave"])
        self.assertIsNone(out["instruments"][0]["regions"][0]["unity_note"])


if __name__ == "__main__":
    unittest.main()
