import unittest

from svxkit.svx import summary

from svx import chunk, form, raw, text, vhdr


class BasicTests(unittest.TestCase):
    def test_plain_instrument(self):
        out = summary(form(vhdr(100, 0, 8000), text(b"NAME", "kick"), chunk(b"BODY", raw([0, 50, -60] + [1] * 97))))
        self.assertEqual((out["name"], out["sample_rate"], out["frames"]), ("kick", 8000, 100))
        self.assertEqual(out["peak"], [60])
        self.assertEqual(out["duration"], "1/80")


if __name__ == "__main__":
    unittest.main()
