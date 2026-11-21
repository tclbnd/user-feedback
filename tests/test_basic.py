import unittest
from fractions import Fraction

from modkit.info import summary

from mods import module, sample


class BasicTests(unittest.TestCase):
    def test_title_and_samples(self):
        out = summary(module("Night Drive", [sample("kick", 0), sample("pad", 0)]))
        self.assertEqual((out["format"], out["signature"], out["title"]), ("31-sample", "M.K.", "Night Drive"))
        self.assertEqual(out["song_length"], 1)
        self.assertEqual(out["patterns"], 1)


class FormatTests(unittest.TestCase):
    def test_channel_tags(self):
        for tag, ch in ((b"6CHN", 6), (b"8CHN", 8), (b"12CH", 12), (b"FLT8", 8), (b"M!K!", 4)):
            out = summary(module("t", [sample("s", 100)], channels=ch, tag=tag))
            self.assertEqual(out["channels"], ch)
            # sample data must start after channels*64*4 pattern bytes
            self.assertEqual(out["sample_bytes_present"], 200)

    def test_fifteen_sample(self):
        out = summary(module("old", [sample("a", 50), sample("b", 0),
                                     sample("c", 10, rep_start=5, rep_len=3)], fifteen=True))
        self.assertEqual((out["format"], out["signature"], out["channels"]), ("15-sample", None, 4))
        self.assertEqual([s["length"] for s in out["samples"]], [100, 20])
        looped = out["samples"][1]
        self.assertEqual((looped["loop_start"], looped["loop_length"], looped["looped"]), (10, 6, True))
        self.assertEqual(out["sample_bytes_present"], 120)

    def test_lengths_in_bytes_and_signed_finetune(self):
        out = summary(module("w", [sample("x", 1000, finetune=0xF9, rep_start=4, rep_len=1)]))
        s = out["samples"][0]
        self.assertEqual((s["length"], s["finetune"]), (2000, -7))
        # a one-word loop is the "no loop" convention
        self.assertEqual((s["loop_start"], s["loop_length"], s["looped"]), (8, 2, False))


class DurationTests(unittest.TestCase):
    def test_plain(self):
        out = summary(module("d", orders=(0, 0)))
        self.assertEqual(out["duration"], str(Fraction(2 * 64 * 6 * 5, 250)))

    def test_speed_change(self):
        out = summary(module("s", patterns=[{(0, 0): (0, 0, 0xF, 3)}]))
        self.assertEqual(out["duration"], str(Fraction(64 * 3 * 5, 250)))

    def test_tempo_change(self):
        out = summary(module("t", patterns=[{(1, 0): (0, 0, 0xF, 250)}]))
        self.assertEqual(out["duration"], str(Fraction(6 * 5, 250) + Fraction(63 * 6 * 5, 500)))

    def test_position_jump_loop_stops_at_revisit(self):
        out = summary(module("j", patterns=[{(10, 0): (0, 0, 0xB, 0)}]))
        self.assertEqual(out["duration"], str(Fraction(11 * 6 * 5, 250)))

    def test_pattern_break(self):
        out = summary(module("b", orders=(0, 1), patterns=[{(0, 0): (0, 0, 0xD, 0x05)}, {}]))
        self.assertEqual(out["duration"], str(Fraction((1 + 59) * 6 * 5, 250)))

    def test_jump_past_song_length(self):
        out = summary(module("e", orders=(0, 0), patterns=[{(0, 0): (0, 0, 0xB, 5)}]))
        self.assertEqual(out["duration"], str(Fraction(6 * 5, 250)))


if __name__ == "__main__":
    unittest.main()
