import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build import adtl, cue, info, inst, plst, smpl, wav  # noqa: E402
from wavkit.riff import WavError, parse  # noqa: E402


class BasicTest(unittest.TestCase):
    def test_markers(self):
        out = parse(wav(cue([(1, 1000), (2, 5000)]), adtl([(1, "Intro"), (2, "Verse")])))
        self.assertEqual(out["frames"], 48000)
        self.assertEqual([(m["id"], m["sample"], m["label"]) for m in out["markers"]], [(1, 1000, "Intro"), (2, 5000, "Verse")])
        self.assertEqual(out["markers"][1]["time"], "5/48")

    def test_plain(self):
        out = parse(wav(frames=100, ch=1, bits=16, rate=44100))
        self.assertEqual((out["channels"], out["bits"], out["frames"], out["markers"]), (1, 16, 100, []))

    def test_not_wav(self):
        with self.assertRaises(WavError):
            parse(b"RIFF\x04\x00\x00\x00AVI ")

    def test_all_zero_position_still_finds_the_markers(self):
        # An editor leaves dwPosition at zero for every point and keeps the
        # real offset in dwSampleOffset; markers must not all land on zero.
        out = parse(wav(cue([(1, 48000, 0), (2, 96000, 0)])))
        self.assertEqual([(m["sample"], m["time"]) for m in out["markers"]],
                         [(48000, "1"), (96000, "2")])

    def test_position_used_when_offset_is_zero(self):
        # Conversely, a writer that only filled dwPosition is still understood.
        out = parse(wav(cue([(1, 0, 4321)])))
        self.assertEqual(out["markers"][0]["sample"], 4321)

    def test_markers_sorted_by_position_then_id(self):
        out = parse(wav(cue([(9, 5000), (3, 1000), (4, 5000), (1, 2000)])))
        self.assertEqual([m["id"] for m in out["markers"]], [3, 1, 4, 9])

    def test_odd_length_label_keeps_following_labels(self):
        # "abc" makes the labl chunk odd-sized; the pad byte must be skipped so
        # the next labl/note/ltxt are still found.
        out = parse(wav(cue([(1, 10), (2, 20), (3, 30)]),
                        adtl(labels=[(1, "abc"), (2, "defg"), (3, "hi")],
                             notes=[(2, "off-axis")],
                             spans=[(3, 100, "region")])))
        got = [(m["id"], m["label"], m["note"], m.get("length")) for m in out["markers"]]
        self.assertEqual(got, [(1, "abc", None, None), (2, "defg", "off-axis", None), (3, "hi", None, 100)])

    def test_loop_length_covers_both_endpoints(self):
        # dwStart/dwEnd name frames inside the region, so a pass from 2000 to
        # 29999 is 28000 frames, not 27999.
        out = parse(wav(smpl([(1, 0, 2000, 29999, 0)])))
        self.assertEqual(out["loops"][0]["length"], 28000)

    def test_loop_play_count_zero_is_infinite(self):
        out = parse(wav(smpl([(1, 0, 0, 9, 0), (2, 0, 0, 9, 3)])))
        self.assertIsNone(out["loops"][0]["play_count"])
        self.assertEqual(out["loops"][1]["play_count"], 3)

    def test_loop_types(self):
        out = parse(wav(smpl([(1, 0, 0, 9, 1), (2, 1, 0, 9, 1), (3, 2, 0, 9, 1), (4, 7, 0, 9, 1)])))
        self.assertEqual([loop["type"] for loop in out["loops"]],
                         ["forward", "alternating", "backward", "vendor"])

    def test_signed_instrument_fields(self):
        out = parse(wav(inst(note=48, detune=-7, gain=-3)))
        self.assertEqual((out["instrument"]["detune_cents"], out["instrument"]["gain_db"]), (-7, -3))

    def test_positive_instrument_fields(self):
        out = parse(wav(inst(note=60, detune=5, gain=6, lo=10, hi=90, lv=5, hv=100)))
        self.assertEqual(out["instrument"], {"note": 60, "detune_cents": 5, "gain_db": 6,
                                             "notes": [10, 90], "velocities": [5, 100]})

    def test_sampler_pitch_and_fractions_use_32_bit_scale(self):
        out = parse(wav(smpl([(1, 0, 0, 9, 0, 0x80000000)], pitch=0x40000000)))
        self.assertEqual(out["sampler"]["pitch_fraction"], "1/4")
        self.assertEqual(out["loops"][0]["end_fraction"], "1/2")

    def test_sampler_manufacturer(self):
        out = parse(wav(smpl([], manufacturer=0x41)))
        self.assertEqual(out["sampler"]["manufacturer"], "41")
        out = parse(wav(smpl([], manufacturer=0x002033)))
        self.assertEqual(out["sampler"]["manufacturer"], "00 20 33")

    def test_sampler_smpte_offset(self):
        out = parse(wav(smpl([], smpte_format=25, smpte_offset=0x01020304)))
        self.assertEqual(out["sampler"]["smpte_offset"], "01:02:03:04")
        # 29 is 30 fps with drop frames, written with a semicolon.
        out = parse(wav(smpl([], smpte_format=29, smpte_offset=0x00010203)))
        self.assertEqual(out["sampler"]["smpte_offset"], "00:01:02;03")
        out = parse(wav(smpl([])))
        self.assertIsNone(out["sampler"]["smpte_offset"])

    def test_playlist(self):
        out = parse(wav(cue([(1, 0), (2, 100)]), plst([(1, 100, 1), (2, 50, 0)])))
        self.assertEqual(out["playlist"], [{"cue": 1, "length": 100, "repeats": 1},
                                           {"cue": 2, "length": 50, "repeats": 0}])

    def test_info(self):
        out = parse(wav(info(INAM="Harbour dawn", IART="Field kit B")))
        self.assertEqual(out["info"], {"INAM": "Harbour dawn", "IART": "Field kit B"})

    def test_bytes_after_riff_end_ignored(self):
        body = wav(cue([(1, 10)]))
        # A well-formed file followed by a bogus (truncated) chunk.
        out = parse(body + b"JUNK\x00\x00\x00")
        self.assertEqual([m["sample"] for m in out["markers"]], [10])


if __name__ == "__main__":
    unittest.main()
