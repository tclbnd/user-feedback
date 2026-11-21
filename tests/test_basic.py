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


if __name__ == "__main__":
    unittest.main()
