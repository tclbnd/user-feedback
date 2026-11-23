import unittest
from fractions import Fraction

from ifokit.ifo import bcd, ptime, parse

from discs import bcd as enc_bcd
from discs import ifo, pgc, ptime as enc_ptime


def audio(codec, channels, lang_code=b"  ", purpose=0, app=0, lpcm_rate=0):
    """Build an 8-byte VTS audio attribute.

    codec: numeric coding mode; channels: real channel count (field is n-1);
    purpose: language type; app: 2-bit application / LPCM quantisation.
    """
    b0 = ((codec & 7) << 5) | ((app & 3) << 3) | (lpcm_rate & 7)
    b1 = ((purpose & 7) << 5) | ((channels - 1) & 7)
    return bytes([b0, b1]) + lang_code.ljust(2, b" ") + bytes(4)


def sub(lang_code=b"  "):
    return b"\x01\x00" + lang_code.ljust(2, b" ") + bytes(2)


class TimeTests(unittest.TestCase):
    def test_bcd(self):
        self.assertEqual(bcd(enc_bcd(42)), 42)
        self.assertEqual(bcd(0), 0)
        self.assertEqual(bcd(0x59), 59)

    def test_pal_uses_25fps(self):
        self.assertEqual(ptime(enc_ptime((0, 0, 0, 10), fps=25)), Fraction(2, 5))

    def test_ntsc_uses_30fps(self):
        self.assertEqual(ptime(enc_ptime((0, 0, 0, 10), fps=30)), Fraction(1, 3))

    def test_bcd_minutes_seconds(self):
        # 1h42m00s10f PAL: mm=42 and ss=00 are BCD, not binary.
        self.assertEqual(ptime(enc_ptime((1, 42, 0, 10), fps=25)),
                         Fraction(3600) + Fraction(42 * 60) + Fraction(10, 25))

    def test_pal_feature_length(self):
        # Misreading PAL frames at 30fps and BCD minutes as binary is the
        # reported "1h42 shown as 2h06" defect.
        raw = enc_ptime((1, 42, 0, 0), fps=25)
        self.assertEqual(ptime(raw), Fraction(6120))
        hh, mm, ss, ff = raw
        wrong = (Fraction(bcd(hh) * 3600) + Fraction(mm * 60 + ss)
                 + Fraction(bcd(ff & 0x3F), 30))
        self.assertEqual(wrong, Fraction(2 * 3600 + 6 * 60))


class VideoAttributeTests(unittest.TestCase):
    def test_pal_4_3(self):
        out = parse(ifo([pgc((0, 0, 5, 0), [(0, 0, 5, 0)], [1])], [[(1, 1)]]))
        self.assertEqual((out["standard"], out["aspect"]), ("PAL", "4:3"))

    def test_ntsc_4_3(self):
        out = parse(ifo([pgc((0, 0, 5, 0), [(0, 0, 5, 0)], [1], fps=30)],
                        [[(1, 1)]], pal=False))
        self.assertEqual((out["standard"], out["aspect"]), ("NTSC", "4:3"))

    def test_pal_16_9(self):
        out = parse(ifo([pgc((0, 0, 5, 0), [(0, 0, 5, 0)], [1])],
                        [[(1, 1)]], wide=True))
        self.assertEqual((out["standard"], out["aspect"]), ("PAL", "16:9"))

    def test_ntsc_16_9(self):
        out = parse(ifo([pgc((0, 0, 5, 0), [(0, 0, 5, 0)], [1], fps=30)],
                        [[(1, 1)]], pal=False, wide=True))
        self.assertEqual((out["standard"], out["aspect"]), ("NTSC", "16:9"))


class AudioTests(unittest.TestCase):
    def test_channel_counts(self):
        out = parse(ifo(
            [pgc((0, 0, 1, 0), [(0, 0, 1, 0)], [1])], [[(1, 1)]],
            audios=[audio(0, 6, b"en"),   # AC-3 5.1 -> 6 channels
                    audio(0, 2, b"de"),   # AC-3 stereo -> 2 channels
                    audio(0, 1)]))        # AC-3 mono -> 1 channel
        self.assertEqual([a["channels"] for a in out["audio"]], [6, 2, 1])
        self.assertEqual([a["codec"] for a in out["audio"]], ["AC-3"] * 3)
        self.assertEqual([a["language"] for a in out["audio"]], ["en", "de", None])
        self.assertEqual([a["sample_rate"] for a in out["audio"]], [48000] * 3)
        self.assertTrue(all(a["bits"] is None for a in out["audio"]))

    def test_codecs_including_dts(self):
        out = parse(ifo(
            [pgc((0, 0, 1, 0), [(0, 0, 1, 0)], [1])], [[(1, 1)]],
            audios=[audio(2, 2), audio(3, 2), audio(4, 2),
                    audio(6, 6), audio(5, 2)]))
        self.assertEqual([a["codec"] for a in out["audio"]],
                         ["MPEG-1", "MPEG-2", "LPCM", "DTS", "type_5"])

    def test_lpcm_quantisation_and_rates(self):
        out = parse(ifo(
            [pgc((0, 0, 1, 0), [(0, 0, 1, 0)], [1])], [[(1, 1)]],
            audios=[audio(4, 2, app=0, lpcm_rate=0),  # 16 bit / 48k
                    audio(4, 2, app=1, lpcm_rate=1),  # 20 bit / 96k
                    audio(4, 2, app=2, lpcm_rate=2),  # 24 bit / 44.1k
                    audio(4, 2, app=3, lpcm_rate=3)]))  # DRC, 32k
        self.assertEqual([a["bits"] for a in out["audio"]], [16, 20, 24, None])
        self.assertEqual([a["sample_rate"] for a in out["audio"]],
                         [48000, 96000, 44100, 32000])

    def test_mpeg_application(self):
        out = parse(ifo(
            [pgc((0, 0, 1, 0), [(0, 0, 1, 0)], [1])], [[(1, 1)]],
            audios=[audio(3, 2, app=1), audio(2, 2, app=2), audio(3, 2, app=0)]))
        self.assertEqual([a["application"] for a in out["audio"]],
                         ["karaoke", "surround", None])
        # AC-3 carries no MPEG application mode.
        out = parse(ifo([pgc((0, 0, 1, 0), [(0, 0, 1, 0)], [1])], [[(1, 1)]],
                        audios=[audio(0, 6, app=2)]))
        self.assertIsNone(out["audio"][0]["application"])

    def test_purpose(self):
        out = parse(ifo(
            [pgc((0, 0, 1, 0), [(0, 0, 1, 0)], [1])], [[(1, 1)]],
            audios=[audio(0, 2, purpose=p) for p in range(5)]))
        self.assertEqual([a["purpose"] for a in out["audio"]],
                         [None, "normal", "visually_impaired",
                          "directors_comments", "alternate_directors_comments"])


class SubpictureTests(unittest.TestCase):
    def test_languages(self):
        out = parse(ifo(
            [pgc((0, 0, 1, 0), [(0, 0, 1, 0)], [1])], [[(1, 1)]],
            subs=[sub(b"en"), sub(), sub(b"fr")]))
        self.assertEqual(out["subpictures"], ["en", None, "fr"])


class ChainTests(unittest.TestCase):
    def test_pal_chain_times(self):
        out = parse(ifo(
            [pgc((0, 1, 30, 0), [(0, 0, 50, 0), (0, 0, 40, 0)], [1, 2])],
            [[(1, 1), (1, 2)]]))
        p = out["pgcs"][0]
        self.assertEqual(p["duration"], "90")
        self.assertEqual(p["cell_total"], "90")
        self.assertEqual(p["program_starts"], ["0", "50"])

    def test_ntsc_chain_times(self):
        out = parse(ifo(
            [pgc((0, 0, 2, 15), [(0, 0, 1, 15), (0, 0, 1, 0)], [1, 2], fps=30)],
            [[(1, 1), (1, 2)]], pal=False))
        p = out["pgcs"][0]
        self.assertEqual(p["duration"], str(Fraction(5, 2)))
        self.assertEqual(p["cell_total"], str(Fraction(5, 2)))
        self.assertEqual(p["program_starts"], ["0", "3/2"])

    def test_program_with_several_cells(self):
        # Two programs over four cells: program 1 = cells 1..3, program 2 = cell 4.
        out = parse(ifo(
            [pgc((0, 0, 40, 0),
                 [(0, 0, 10, 0), (0, 0, 10, 0), (0, 0, 10, 0), (0, 0, 10, 0)],
                 [1, 4])],
            [[(1, 1), (1, 2)]]))
        p = out["pgcs"][0]
        self.assertEqual(p["program_starts"], ["0", "30"])
        self.assertEqual(out["titles"][0]["chapter_starts"], ["0", "30"])

    def test_angle_block_counted_once(self):
        # Cells 2..4 are a three-angle block (block modes start/middle/end);
        # only its first cell's time belongs to the timeline.
        out = parse(ifo(
            [pgc((0, 1, 0, 0),
                 [(0, 0, 10, 0), (0, 0, 20, 0), (0, 0, 20, 0),
                  (0, 0, 20, 0), (0, 0, 5, 0)],
                 [1, 5], blocks=(0, 1, 2, 3, 0))],
            [[(1, 1), (1, 2)]]))
        p = out["pgcs"][0]
        self.assertEqual(p["cell_total"], "35")  # 10 + 20 + 5
        self.assertEqual(p["program_starts"], ["0", "30"])
        self.assertEqual(out["titles"][0]["chapter_starts"], ["0", "30"])

    def test_still_cells(self):
        out = parse(ifo(
            [pgc((0, 0, 10, 0), [(0, 0, 5, 0), (0, 0, 5, 0)], [1, 2],
                 stills=(0, 10))],
            [[(1, 1), (1, 2)]]))
        self.assertEqual(out["pgcs"][0]["still_cells"], [2])

    def test_mixed_fps_titles(self):
        pal = parse(ifo([pgc((0, 0, 1, 0), [(0, 0, 1, 0)], [1])], [[(1, 1)]]))
        ntsc = parse(ifo([pgc((0, 0, 1, 0), [(0, 0, 1, 0)], [1], fps=30)],
                         [[(1, 1)]], pal=False))
        self.assertEqual(pal["pgcs"][0]["duration"], "1")
        self.assertEqual(ntsc["pgcs"][0]["duration"], "1")
        frame = parse(ifo([pgc((0, 0, 0, 12), [(0, 0, 0, 12)], [1])],
                          [[(1, 1)]]))
        self.assertEqual(frame["pgcs"][0]["duration"], str(Fraction(12, 25)))


class StructureTests(unittest.TestCase):
    def test_basic_structure_unchanged(self):
        out = parse(ifo(
            [pgc((0, 0, 9, 0), [(0, 0, 4, 0), (0, 0, 5, 0)], [1, 2])],
            [[(1, 1), (1, 2)]]))
        self.assertEqual((out["pgcs"][0]["programs"],
                          out["pgcs"][0]["cells"],
                          out["titles"][0]["chapters"]), (2, 2, 2))

    def test_titles_reference_chains_and_programs(self):
        out = parse(ifo(
            [pgc((0, 0, 4, 0), [(0, 0, 4, 0)], [1]),
             pgc((0, 0, 9, 0), [(0, 0, 4, 0), (0, 0, 5, 0)], [1, 2])],
            [[(1, 1)], [(2, 1), (2, 2)]]))
        self.assertEqual(out["titles"][0]["chapter_starts"], ["0"])
        self.assertEqual(out["titles"][1]["chapter_starts"], ["0", "4"])
