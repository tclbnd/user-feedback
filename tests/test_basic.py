import unittest

from cuesplit import naming
from cuesplit.sheet import CueError, msf, parse
from cuesplit.tracks import layout


class BasicTests(unittest.TestCase):
    def test_two_tracks(self):
        album = parse('FILE "a.wav" WAVE\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n  TRACK 02 AUDIO\n    INDEX 01 03:00:00\n')
        t = layout(album, {"a.wav": 44100 * 300})
        self.assertEqual([(x["start"], x["end"]) for x in t], [(0, 44100 * 180), (44100 * 180, 44100 * 300)])

    def test_names(self):
        self.assertEqual(naming.unique(["a.flac", "a.flac"]), ["a.flac", "a (1).flac"])
        self.assertEqual(naming.track_name({"number": 3, "title": "AC/DC?"}), "03 - AC_DC_.flac")


class TimeTests(unittest.TestCase):
    def test_frames_are_75_per_second(self):
        # INDEX 01 02:37:74: the old parser used 1/100 s frames and cut ~11 ms early.
        self.assertEqual(msf("02:37:74"), (2 * 60 + 37) * 44100 + 74 * 588)

    def test_past_99_minutes(self):
        self.assertEqual(msf("120:00:00"), 120 * 60 * 44100)
        self.assertEqual(msf("100:00:00"), 100 * 60 * 44100)

    def test_bad_times_are_errors(self):
        for bad in ("1:2:3", "00:60:00", "00:00:75", "00:00", "aa:bb:cc", "00:00:0x", ""):
            with self.assertRaises(CueError, msg=bad):
                msf(bad)


class ParseTests(unittest.TestCase):
    def test_quoted_values_keep_spaces(self):
        album = parse('TITLE "Harbour Lights"\nPERFORMER "Mira Sol"\n'
                      'FILE "Harbour Lights.wav" WAVE\n'
                      '  TRACK 01 AUDIO\n'
                      '    TITLE "Low Tide, High Hopes"\n'
                      '    PERFORMER "Mira Sol & Tomas Vey"\n'
                      '    ISRC QZDA82600001\n'
                      '    INDEX 01 00:00:00\n')
        self.assertEqual(album["title"], "Harbour Lights")
        self.assertEqual(album["performer"], "Mira Sol")
        track = album["files"][0]["tracks"][0]
        self.assertEqual(album["files"][0]["name"], "Harbour Lights.wav")
        self.assertEqual(track["title"], "Low Tide, High Hopes")
        self.assertEqual(track["performer"], "Mira Sol & Tomas Vey")
        self.assertEqual(track["isrc"], "QZDA82600001")

    def test_performer_inherited_from_album(self):
        album = parse('PERFORMER "Album Artist"\nFILE "a.wav" WAVE\n'
                      '  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
                      '  TRACK 02 AUDIO\n    PERFORMER "Guest"\n    INDEX 01 01:00:00\n')
        out = layout(album, {"a.wav": 44100 * 120})
        self.assertEqual([t["performer"] for t in out], ["Album Artist", "Guest"])

    def test_rem_and_gaps(self):
        album = parse('REM GENRE "Folk"\nFILE "a.wav" WAVE\n'
                      '  TRACK 01 AUDIO\n    PREGAP 00:01:00\n'
                      '    INDEX 01 00:00:00\n'
                      '  TRACK 02 AUDIO\n    INDEX 01 01:00:00\n'
                      '    POSTGAP 00:02:00\n')
        self.assertEqual(album["rem"]["GENRE"], "Folk")
        out = layout(album, {"a.wav": 44100 * 180})
        self.assertEqual(out[0]["pregap"], 44100)
        self.assertEqual(out[1]["postgap"], 88200)


class LayoutTests(unittest.TestCase):
    def test_gap_belongs_to_previous_track(self):
        album = parse('FILE "a.wav" WAVE\n'
                      '  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
                      '  TRACK 02 AUDIO\n    INDEX 00 00:02:00\n    INDEX 01 00:04:00\n')
        out = layout(album, {"a.wav": 44100 * 300})
        self.assertEqual(out[0]["end"], 44100 * 4)
        self.assertEqual(out[0]["length"], 44100 * 4)
        self.assertEqual(out[1]["start"], 44100 * 4)
        self.assertEqual(out[1]["pregap"], 44100 * 2)

    def test_second_disc_has_its_own_clock(self):
        album = parse('FILE "CD1.wav" WAVE\n'
                      '  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
                      '  TRACK 02 AUDIO\n    INDEX 01 41:10:00\n'
                      'FILE "CD2.wav" WAVE\n'
                      '  TRACK 03 AUDIO\n    INDEX 01 00:00:32\n')
        out = layout(album, {"CD1.wav": 44100 * 3600, "CD2.wav": 44100 * 600})
        self.assertEqual(out[1]["file"], "CD1.wav")
        self.assertEqual(out[1]["end"], 44100 * 3600)
        self.assertEqual(out[2]["file"], "CD2.wav")
        self.assertEqual(out[2]["start"], 32 * 588)
        self.assertEqual(out[2]["end"], 44100 * 600)
        self.assertEqual(out[2]["length"], 44100 * 600 - 32 * 588)

    def test_gap_across_two_files(self):
        album = parse('FILE "CD1.wav" WAVE\n'
                      '  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
                      '  TRACK 02 AUDIO\n    INDEX 00 41:10:00\n'
                      'FILE "CD2.wav" WAVE\n'
                      '    INDEX 01 00:00:32\n'
                      '  TRACK 03 AUDIO\n    INDEX 01 10:00:00\n')
        out = layout(album, {"CD1.wav": 44100 * 3000, "CD2.wav": 44100 * 1200})
        # The gap starts in CD1 and ends in CD2; its head is the tail of CD1.
        expected = (44100 * 3000 - (41 * 60 + 10) * 44100) + 32 * 588
        self.assertEqual(out[1]["pregap"], expected)
        self.assertEqual(out[1]["file"], "CD2.wav")
        self.assertEqual(out[1]["start"], 32 * 588)
        self.assertEqual(out[0]["end"], 44100 * 3000)

    def test_missing_index_01_is_an_error(self):
        with self.assertRaises(CueError):
            parse('FILE "a.wav" WAVE\n  TRACK 01 AUDIO\n    INDEX 00 00:00:00\n')


if __name__ == "__main__":
    unittest.main()
