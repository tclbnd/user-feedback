import unittest

from svxkit.svx import SvxError, summary

from svx import DELTAS, chan, chunk, fib_pack, form, raw, text, vhdr


class BasicTests(unittest.TestCase):
    def test_plain_instrument(self):
        out = summary(form(vhdr(100, 0, 8000), text(b"NAME", "kick"),
                           chunk(b"BODY", raw([0, 50, -60] + [1] * 97))))
        self.assertEqual((out["name"], out["sample_rate"], out["frames"]),
                         ("kick", 8000, [100]))
        self.assertEqual(out["peak"], [60])
        self.assertEqual(out["head"], [[0, 50, -60, 1]])
        self.assertEqual(out["duration"], "1/80")
        self.assertEqual(out["compression"], "none")
        self.assertEqual(out["volume"], 1.0)
        self.assertEqual((out["channels"], out["channel"]), (1, "mono"))
        self.assertEqual(out["samples_per_octave"], [100])
        self.assertEqual(out["one_shot"], 100)
        self.assertEqual(out["repeat"], 0)

    def test_odd_length_text_chunk_keeps_following_chunks_aligned(self):
        # "Strings" is 7 bytes: the chunk gets one pad byte, so BODY must
        # still be found and decoded correctly.
        data = form(vhdr(4, 0, 1000), text(b"NAME", "Strings"),
                    chunk(b"BODY", raw([1, -2, 3, -4])))
        out = summary(data)
        self.assertEqual(out["name"], "Strings")
        self.assertEqual(out["frames"], [4])
        self.assertEqual(out["head"], [[1, -2, 3, -4]])
        self.assertEqual(out["peak"], [4])

    def test_all_annotations_are_kept_in_order(self):
        data = form(vhdr(2, 0, 1000),
                    text(b"ANNO", "take 1"),
                    text(b"ANNO", "fixed loop"),
                    chunk(b"BODY", raw([0, 0])))
        out = summary(data)
        self.assertEqual(out["annotations"], ["take 1", "fixed loop"])

    def test_name_and_author(self):
        data = form(vhdr(1, 0, 1000), text(b"NAME", "Bass B"),
                    text(b"AUTH", "studio"), chunk(b"BODY", raw([7])))
        out = summary(data)
        self.assertEqual((out["name"], out["author"]), ("Bass B", "studio"))

    def test_volume_is_16_16_fixed_point(self):
        out = summary(form(vhdr(1, 0, 1000, volume=0x8000),
                           chunk(b"BODY", raw([0]))))
        self.assertEqual(out["volume"], 0.5)
        out = summary(form(vhdr(1, 0, 1000, volume=0xC000),
                           chunk(b"BODY", raw([0]))))
        self.assertEqual(out["volume"], 0.75)

    def test_octave_sample_counts_halve_from_the_highest(self):
        # Highest octave: 400 one-shot + 800 repeat = 1200 samples,
        # followed by 600 and 300 sample octaves.
        body = raw([0] * (1200 + 600 + 300))
        out = summary(form(vhdr(400, 800, 16726, octaves=3),
                           chunk(b"BODY", body)))
        self.assertEqual(out["octaves"], 3)
        self.assertEqual(out["samples_per_octave"], [1200, 600, 300])
        self.assertEqual(out["frames"], [2100])
        self.assertEqual(out["duration"], "600/8363")

    def test_fibonacci_delta_decoding(self):
        # Initial sample 10, then deltas +1, +2, +3, +5 (codes 9..12).
        packed = bytes([0, 10, (9 << 4) | 10, (11 << 4) | 12])
        out = summary(form(vhdr(5, 0, 8000, comp=1), chunk(b"BODY", packed)))
        self.assertEqual(out["compression"], "fibonacci")
        self.assertEqual(out["frames"], [5])
        self.assertEqual(out["head"][0], [10, 11, 13, 16])
        self.assertEqual(out["peak"], [21])

    def test_fibonacci_delta_table_and_negative_deltas(self):
        codes = [0, 1, 7, 8, 15]
        out = summary(form(vhdr(7, 0, 8000, comp=1),
                           chunk(b"BODY", fib_pack(0, codes))))
        expected = [0]
        for code in codes:
            expected.append(expected[-1] + DELTAS[code])
        self.assertEqual(out["head"][0], expected[:4])
        self.assertEqual(out["frames"], [7])
        self.assertEqual(out["peak"], [max(abs(s) for s in expected)])

    def test_fibonacci_odd_nibble_pad_is_dropped(self):
        # 5 delta codes -> an odd nibble count; the packer appends code 8.
        packed = fib_pack(5, [9, 10, 11, 12, 13])
        self.assertEqual(len(packed), 5)  # pad + initial + 3 nibble bytes
        out = summary(form(vhdr(6, 0, 8000, comp=1), chunk(b"BODY", packed)))
        # 5 real deltas -> initial plus five samples; the nibble pad is cut.
        self.assertEqual(out["frames"], [6])
        self.assertEqual(out["head"][0], [5, 6, 8, 11])
        self.assertEqual(out["peak"], [24])

    def test_chan_left_right_and_stereo_planar_body(self):
        for flags, count, label in ((0x20, 1, "left"), (0x10, 1, "right")):
            out = summary(form(vhdr(2, 0, 8000), chan(flags),
                               chunk(b"BODY", raw([1, 2]))))
            self.assertEqual((out["channels"], out["channel"]), (count, label))
            self.assertEqual(out["frames"], [2])

        # Stereo BODY: left block then right block, no interleaving.
        out = summary(form(vhdr(3, 0, 8000), chan(0x30),
                           chunk(b"BODY", raw([10, 20, 30, -40, -50, 60]))))
        self.assertEqual((out["channels"], out["channel"]), (2, "stereo"))
        self.assertEqual(out["frames"], [3, 3])
        self.assertEqual(out["head"], [[10, 20, 30], [-40, -50, 60]])
        self.assertEqual(out["peak"], [30, 60])
        self.assertEqual(out["samples_per_octave"], [3])

    def test_stereo_fibonacci_planar_body(self):
        # 16 real delta codes plus the initial sample give 17 raw samples;
        # the VHDR declares 16, which split into two 8 sample channel blocks.
        packed = fib_pack(0, list(range(16)))
        out = summary(form(vhdr(16, 0, 8000, comp=1), chan(0x30),
                           chunk(b"BODY", packed)))
        self.assertEqual((out["channels"], out["channel"]), (2, "stereo"))
        self.assertEqual(out["compression"], "fibonacci")
        self.assertEqual(out["frames"], [8, 8])
        self.assertEqual(len(out["head"]), 2)
        self.assertEqual(len(out["peak"]), 2)

    def test_form_level_padding_after_odd_final_chunk(self):
        # A BODY of 3 bytes is odd; its pad byte must not be mistaken for
        # another chunk, and FORM scanning must end cleanly.
        data = form(text(b"NAME", "ab"), vhdr(3, 0, 1000),
                    chunk(b"BODY", raw([1, 2, 3])))
        self.assertEqual(len(data) & 1, 0)
        out = summary(data)
        self.assertEqual(out["name"], "ab")
        self.assertEqual(out["frames"], [3])

    def test_structural_and_missing_chunk_errors(self):
        with self.assertRaises(SvxError):
            summary(b"XXXX" + b"\0" * 8)
        with self.assertRaises(SvxError):
            summary(b"FORM" + b"\0\0\0\0" + b"RIFF")
        with self.assertRaises(SvxError):
            summary(form(text(b"NAME", "x")))            # no BODY
        with self.assertRaises(SvxError):
            summary(form(chunk(b"BODY", raw([1]))))      # no VHDR
        with self.assertRaises(SvxError):
            summary(form(vhdr(1, 0, 1000), chan(0x1),
                         chunk(b"BODY", raw([1]))))       # bad CHAN flags

    def test_zero_rate_has_no_duration(self):
        out = summary(form(vhdr(3, 0, 0), chunk(b"BODY", raw([1, 2, 3]))))
        self.assertIsNone(out["duration"])
        self.assertEqual(out["frames"], [3])

    def test_examples(self):
        with open("examples/bass.8svx", "rb") as fh:
            bass = summary(fh.read())
        self.assertEqual(bass["name"], "Bass B")
        self.assertEqual(bass["author"], "studio")
        self.assertEqual(bass["annotations"], ["take 1", "fixed loop"])
        self.assertEqual(bass["sample_rate"], 16726)
        self.assertEqual((bass["one_shot"], bass["repeat"]), (400, 800))
        self.assertEqual(bass["octaves"], 3)
        self.assertEqual(bass["samples_per_octave"], [1200, 600, 300])
        self.assertEqual(bass["frames"], [8400])
        self.assertEqual(bass["volume"], 0.75)
        self.assertEqual(bass["compression"], "none")
        self.assertEqual(bass["duration"], "600/8363")

        with open("examples/strings.8svx", "rb") as fh:
            strings = summary(fh.read())
        self.assertEqual(strings["name"], "Strings")
        self.assertEqual(strings["compression"], "fibonacci")
        self.assertEqual(strings["sample_rate"], 22050)
        self.assertEqual(strings["frames"], [3000])
        self.assertEqual(strings["volume"], 0.5)
        # The first BODY nibbles are 0,5,10,15,...; walking the delta
        # table rebuilds a smooth low-amplitude waveform rather than noise.
        self.assertEqual(strings["head"], [[0, -34, -37, -35]])
        codes = [0, 5, 10, 15, 4, 9, 14, 3, 8, 13, 2]
        expected = [0]
        for code in codes:
            expected.append(expected[-1] + DELTAS[code])
        self.assertEqual(expected[:4], strings["head"][0])
        self.assertEqual(max(abs(s) for s in expected), 37)


if __name__ == "__main__":
    unittest.main()
