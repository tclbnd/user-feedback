import unittest
from pathlib import Path

from cuesplit.sheet import CueError, msf, parse
from cuesplit.tracks import layout

S = 44100
FRAME = 588
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def example(name):
    return parse((EXAMPLES / name).read_text(encoding="utf-8"))


class ExampleTests(unittest.TestCase):
    def test_harbour(self):
        album = example("harbour.cue")
        self.assertEqual(album["title"], "Harbour Lights")
        self.assertEqual(album["performer"], "Mira Sol")
        tracks = layout(album, {"Harbour Lights.wav": 105840000})
        self.assertEqual([t["title"] for t in tracks],
                         ["Low Tide, High Hopes", "Lantern Song", "Undertow", "Coda (Live)"])
        self.assertEqual([t["start"] for t in tracks],
                         [0, msf("04:13:40"), msf("08:20:17"), msf("36:00:00")])
        self.assertEqual([t["end"] for t in tracks],
                         [msf("04:13:40"), msf("08:20:17"), msf("36:00:00"), 105840000])
        self.assertEqual(tracks[1]["pregap"], msf("04:13:40") - msf("04:11:52"))
        self.assertEqual(tracks[2]["pregap"], msf("00:01:00"))
        self.assertEqual(tracks[0]["performer"], "Mira Sol")
        self.assertEqual(tracks[2]["performer"], "Mira Sol & Tomas Vey")
        self.assertEqual(tracks[0]["isrc"], "QZDA82600001")

    def test_two_discs(self):
        album = example("two_discs.cue")
        self.assertEqual(album["title"], "Two Discs")
        tracks = layout(album, {"CD1.flac": 317520000, "CD2.flac": 400000000})
        self.assertEqual([t["file"] for t in tracks], ["CD1.flac", "CD2.flac", "CD2.flac"])
        self.assertEqual(tracks[0]["start"], 0)
        self.assertEqual(tracks[0]["end"], 317520000)
        self.assertEqual(tracks[1]["start"], msf("00:00:32"))
        self.assertEqual(tracks[1]["end"], msf("120:00:00"))
        self.assertEqual(tracks[2]["start"], msf("120:00:00"))


class TimeTests(unittest.TestCase):
    def test_frames_are_seventy_fifths(self):
        # ff counts CD frames of 1/75 s, not hundredths of a second.
        self.assertEqual(msf("02:37:74"), (2 * 60 + 37) * S + 74 * FRAME)
        self.assertEqual(msf("00:00:01"), FRAME)
        self.assertEqual(msf("00:01:00"), S)
        self.assertEqual(msf("01:00:00"), 60 * S)

    def test_bad_times(self):
        for text in ("", "00:00", "00:00:75", "00:60:00", "1:2", "aa:bb:cc", "-01:00:00", "00:00:1x"):
            with self.assertRaises(CueError, msg=text):
                msf(text)


class ParsingTests(unittest.TestCase):
    def test_strings_keep_spaces(self):
        album = parse(
            'TITLE "Two Discs"\n'
            'PERFORMER "Night Shift"\n'
            'FILE "Harbour Lights.wav" WAVE\n'
            '  TRACK 01 AUDIO\n'
            '    TITLE "Low Tide, High Hopes"\n'
            '    PERFORMER "Mira Sol & Tomas Vey"\n'
            '    INDEX 01 00:00:00\n'
        )
        self.assertEqual(album["title"], "Two Discs")
        self.assertEqual(album["performer"], "Night Shift")
        self.assertEqual(album["files"][0]["name"], "Harbour Lights.wav")
        track = album["files"][0]["tracks"][0]
        self.assertEqual(track["title"], "Low Tide, High Hopes")
        self.assertEqual(track["performer"], "Mira Sol & Tomas Vey")

    def test_doubled_quote(self):
        album = parse('FILE "a.wav" WAVE\n TRACK 01 AUDIO\n TITLE "Say ""Hi"""\n INDEX 01 00:00:00\n')
        self.assertEqual(album["files"][0]["tracks"][0]["title"], 'Say "Hi"')

    def test_performer_falls_back_to_album(self):
        album = parse(
            'PERFORMER "Night Shift"\n'
            'FILE "a.wav" WAVE\n'
            '  TRACK 01 AUDIO\n    TITLE "One"\n    INDEX 01 00:00:00\n'
            '  TRACK 02 AUDIO\n    TITLE "Two"\n    PERFORMER "Guest"\n    INDEX 01 01:00:00\n'
            '  TRACK 03 AUDIO\n    TITLE "Three"\n    INDEX 01 02:00:00\n'
        )
        tracks = layout(album)
        self.assertEqual([t["performer"] for t in tracks], ["Night Shift", "Guest", "Night Shift"])

    def test_isrc_and_type(self):
        album = parse('FILE "a.wav" WAVE\n  TRACK 01 AUDIO\n    ISRC QZDA82600001\n    INDEX 01 00:00:00\n')
        track = layout(album)[0]
        self.assertEqual(track["isrc"], "QZDA82600001")
        self.assertEqual(track["type"], "AUDIO")

    def test_bad_index_number(self):
        with self.assertRaises(CueError):
            parse('FILE "a.wav" WAVE\n  TRACK 01 AUDIO\n    INDEX x1 00:00:00\n')

    def test_bad_track_number(self):
        for number in ("AA", "0", "100", "-1"):
            with self.assertRaises(CueError, msg=number):
                parse('FILE "a.wav" WAVE\n  TRACK ' + number + ' AUDIO\n    INDEX 01 00:00:00\n')

    def test_unquoted_file_name(self):
        album = parse("FILE My Album.wav WAVE\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n")
        self.assertEqual(album["files"][0]["name"], "My Album.wav")
        self.assertEqual(album["files"][0]["type"], "WAVE")

    def test_file_type_optional(self):
        album = parse('FILE "a.wav"\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n')
        self.assertEqual(album["files"][0]["name"], "a.wav")

    def test_crlf_and_bom(self):
        text = '﻿TITLE "Album"\r\nFILE "a.wav" WAVE\r\n  TRACK 01 AUDIO\r\n    INDEX 01 00:00:00\r\n'
        album = parse(text)
        self.assertEqual(album["title"], "Album")
        self.assertEqual(layout(album)[0]["start"], 0)

    def test_lowercase_commands(self):
        album = parse('file "a.wav" wave\n  track 01 audio\n    index 01 00:00:00\n')
        self.assertEqual(album["files"][0]["tracks"][0]["indexes"][1], 0)

    def test_album_tags_before_file_only(self):
        album = parse(
            'TITLE "Album"\n'
            'PERFORMER "Band"\n'
            'FILE "a.wav" WAVE\n'
            '  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
        )
        self.assertEqual(album["title"], "Album")
        self.assertEqual(layout(album)[0]["performer"], "Band")


class BoundaryTests(unittest.TestCase):
    def test_gap_belongs_to_previous_track(self):
        album = parse(
            'FILE "a.wav" WAVE\n'
            '  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
            '  TRACK 02 AUDIO\n    INDEX 00 00:02:00\n    INDEX 01 00:04:00\n'
        )
        one, two = layout(album, {"a.wav": 10 * S})
        self.assertEqual((one["start"], one["end"]), (0, 4 * S))
        self.assertEqual((two["start"], two["end"]), (4 * S, 10 * S))
        self.assertEqual(two["pregap"], 2 * S)
        self.assertEqual(one["pregap"], 0)
        self.assertEqual(two["length"], 6 * S)

    def test_start_is_index_01(self):
        album = parse(
            'FILE "a.wav" WAVE\n'
            '  TRACK 01 AUDIO\n    INDEX 00 04:11:52\n    INDEX 01 04:13:40\n'
        )
        track = layout(album, {"a.wav": 5 * 60 * S})[0]
        self.assertEqual(track["start"], msf("04:13:40"))
        self.assertEqual(track["pregap"], msf("04:13:40") - msf("04:11:52"))

    def test_pregap_command(self):
        album = parse(
            'FILE "a.wav" WAVE\n'
            '  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
            '  TRACK 02 AUDIO\n    PREGAP 00:02:00\n    INDEX 01 03:00:00\n'
        )
        self.assertEqual(layout(album)[1]["pregap"], 2 * S)

    def test_file_boundaries(self):
        album = parse(
            'FILE "CD1.wav" WAVE\n'
            '  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
            '  TRACK 02 AUDIO\n    INDEX 01 03:00:00\n'
            'FILE "CD2.wav" WAVE\n'
            '  TRACK 03 AUDIO\n    INDEX 01 00:00:00\n'
            '  TRACK 04 AUDIO\n    INDEX 01 05:00:00\n'
        )
        tracks = layout(album, {"CD1.wav": 4 * 60 * S, "CD2.wav": 6 * 60 * S})
        self.assertEqual([t["file"] for t in tracks], ["CD1.wav", "CD1.wav", "CD2.wav", "CD2.wav"])
        self.assertEqual([t["start"] for t in tracks], [0, 3 * 60 * S, 0, 5 * 60 * S])
        self.assertEqual([t["end"] for t in tracks], [3 * 60 * S, 4 * 60 * S, 5 * 60 * S, 6 * 60 * S])
        self.assertEqual([t["length"] for t in tracks], [3 * 60 * S, 60 * S, 5 * 60 * S, 60 * S])

    def test_second_disc_restarts_at_zero(self):
        album = parse(
            'FILE "CD1.wav" WAVE\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
            'FILE "CD2.wav" WAVE\n  TRACK 02 AUDIO\n    INDEX 01 00:02:00\n'
        )
        tracks = layout(album)
        self.assertEqual(tracks[1]["file"], "CD2.wav")
        self.assertEqual(tracks[1]["start"], 2 * S)

    def test_gap_across_file_boundary(self):
        # INDEX 00 closes the first image, INDEX 01 opens the second.
        album = parse(
            'FILE "CD1.wav" WAVE\n'
            '  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
            '  TRACK 02 AUDIO\n    INDEX 00 41:00:00\n'
            'FILE "CD2.wav" WAVE\n'
            '    INDEX 01 00:02:00\n'
            '  TRACK 03 AUDIO\n    INDEX 01 10:00:00\n'
        )
        one, two, three = layout(album, {"CD1.wav": 41 * 60 * S + 30 * S, "CD2.wav": 20 * 60 * S})
        self.assertEqual(one["end"], 41 * 60 * S + 30 * S)
        self.assertEqual((two["file"], two["start"]), ("CD2.wav", 2 * S))
        self.assertEqual(two["pregap"], 30 * S + 2 * S)
        self.assertEqual(two["end"], 10 * 60 * S)
        self.assertEqual(three["start"], 10 * 60 * S)

    def test_gap_across_file_boundary_repeated_track(self):
        # Same layout, but the TRACK command is repeated for the second image.
        album = parse(
            'FILE "CD1.wav" WAVE\n'
            '  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
            '  TRACK 02 AUDIO\n    TITLE "Bridge"\n    INDEX 00 41:00:00\n'
            'FILE "CD2.wav" WAVE\n'
            '  TRACK 02 AUDIO\n    INDEX 01 00:02:00\n'
            '  TRACK 03 AUDIO\n    INDEX 01 10:00:00\n'
        )
        tracks = layout(album, {"CD1.wav": 41 * 60 * S + 30 * S})
        self.assertEqual([t["number"] for t in tracks], [1, 2, 3])
        two = tracks[1]
        self.assertEqual(two["title"], "Bridge")
        self.assertEqual((two["file"], two["start"]), ("CD2.wav", 2 * S))
        self.assertEqual(two["pregap"], 30 * S + 2 * S)

    def test_missing_index_01(self):
        album = parse('FILE "a.wav" WAVE\n  TRACK 01 AUDIO\n    INDEX 00 00:02:00\n')
        with self.assertRaises(CueError):
            layout(album)

    def test_index_01_before_index_00(self):
        album = parse('FILE "a.wav" WAVE\n  TRACK 01 AUDIO\n    INDEX 00 00:04:00\n    INDEX 01 00:02:00\n')
        with self.assertRaises(CueError):
            layout(album)

    def test_index_01_goes_backwards(self):
        album = parse(
            'FILE "a.wav" WAVE\n'
            '  TRACK 01 AUDIO\n    INDEX 01 00:05:00\n'
            '  TRACK 02 AUDIO\n    INDEX 01 00:02:00\n'
        )
        with self.assertRaises(CueError):
            layout(album)

    def test_start_past_end_of_file(self):
        album = parse('FILE "a.wav" WAVE\n  TRACK 01 AUDIO\n    INDEX 01 00:10:00\n')
        with self.assertRaises(CueError):
            layout(album, {"a.wav": 5 * S})

    def test_last_track_without_length(self):
        album = parse('FILE "a.wav" WAVE\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n')
        track = layout(album)[0]
        self.assertIsNone(track["end"])
        self.assertIsNone(track["length"])


if __name__ == "__main__":
    unittest.main()
