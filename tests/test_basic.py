import unittest

from sf2kit.info import summary

from banks import sf2


class BasicTests(unittest.TestCase):
    def test_bank_name(self):
        S = [("Kick", 0, 500, 0, 0, 22050, 36, 0, 1)]
        out = summary(sf2("Drums", (2, 1), S, [("Kit", [[(53, 0)]])], [("Standard", 0, 128, [[(41, 0)]])]))
        self.assertEqual(out["name"], "Drums")
        self.assertEqual(out["presets"][0]["name"], "Standard")
        self.assertEqual(out["presets"][0]["bank"], 128)
        self.assertEqual(out["instruments"][0]["samples"], ["Kick"])

    def test_no_terminal_records(self):
        S = [("Kick", 0, 500, 0, 0, 22050, 36, 0, 1)]
        out = summary(sf2("Drums", (2, 1), S, [("Kit", [[(53, 0)]])], [("Standard", 0, 128, [[(41, 0)]])]))
        self.assertEqual([p["name"] for p in out["presets"]], ["Standard"])
        self.assertEqual([i["name"] for i in out["instruments"]], ["Kit"])
        self.assertEqual([s["name"] for s in out["samples"]], ["Kick"])

    def test_version_keeps_minor(self):
        S = [("Kick", 0, 500, 0, 0, 22050, 36, 0, 1)]
        out = summary(sf2("Drums", (2, 4), S, [("Kit", [[(53, 0)]])], [("Standard", 0, 128, [[(41, 0)]])]))
        self.assertEqual(out["version"], "2.04")

    def test_frames_exclude_end(self):
        S = [("Kick", 0, 500, 0, 0, 22050, 36, 0, 1)]
        out = summary(sf2("Drums", (2, 1), S, [("Kit", [[(53, 0)]])], [("Standard", 0, 128, [[(41, 0)]])]))
        self.assertEqual(out["samples"][0]["frames"], 500)

    def test_loop_points_are_relative(self):
        S = [("Loop", 1000, 2000, 1100, 1900, 22050, 60, -3, 1)]
        out = summary(sf2("Bank", (2, 1), S, [("Inst", [[(53, 0)]])], [("Preset", 0, 0, [[(41, 0)]])]))
        self.assertEqual(out["samples"][0]["loop_start"], 100)
        self.assertEqual(out["samples"][0]["loop_end"], 900)

    def test_no_loop_stays_zero(self):
        S = [("Hit", 2500, 3000, 0, 0, 22050, 40, 0, 1)]
        out = summary(sf2("Bank", (2, 1), S, [("Inst", [[(53, 0)]])], [("Preset", 0, 0, [[(41, 0)]])]))
        self.assertEqual(out["samples"][0]["loop_start"], 0)
        self.assertEqual(out["samples"][0]["loop_end"], 0)

    def test_correction_is_signed(self):
        S = [("Kick", 0, 500, 0, 0, 22050, 36, -20, 1)]
        out = summary(sf2("Bank", (2, 1), S, [("Inst", [[(53, 0)]])], [("Preset", 0, 0, [[(41, 0)]])]))
        self.assertEqual(out["samples"][0]["correction"], -20)

    def test_key_range_bytes(self):
        S = [("Kick", 0, 500, 0, 0, 22050, 36, 0, 1)]
        out = summary(sf2("Bank", (2, 1), S, [("Inst", [[(43, (36, 40)), (53, 0)]])],
                          [("Preset", 0, 0, [[(41, 0)]])]))
        self.assertEqual(out["instruments"][0]["key_ranges"], ["36-40"])

    def test_key_range_default(self):
        S = [("Kick", 0, 500, 0, 0, 22050, 36, 0, 1)]
        out = summary(sf2("Bank", (2, 1), S, [("Inst", [[(53, 0)]])], [("Preset", 0, 0, [[(41, 0)]])]))
        self.assertEqual(out["instruments"][0]["key_ranges"], ["0-127"])

    def test_global_zone_excluded(self):
        S = [("Kick", 0, 500, 0, 0, 22050, 36, 0, 1)]
        inst = [("Inst", [[(48, 1200)], [(53, 0)]])]
        pres = [("Preset", 0, 0, [[(48, 1200)], [(41, 0)]])]
        out = summary(sf2("Bank", (2, 1), S, inst, pres))
        self.assertTrue(out["instruments"][0]["global_zone"])
        self.assertEqual(out["instruments"][0]["zones"], 1)
        self.assertEqual(out["instruments"][0]["key_ranges"], ["0-127"])
        self.assertTrue(out["presets"][0]["global_zone"])
        self.assertEqual(out["presets"][0]["zones"], 1)


if __name__ == "__main__":
    unittest.main()
