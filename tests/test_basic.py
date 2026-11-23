import unittest

from cplkit.cpl import parse

from playlists import CPL_2016, cpl


class BasicTests(unittest.TestCase):
    def test_title(self):
        out = parse(cpl([24]))
        self.assertEqual((out["title"], out["edit_rate"], out["segments"]), ("TEST", "24", 1))

    def test_duration(self):
        self.assertEqual(parse(cpl([240, 480]))["duration"], "30")


class St20673Tests(unittest.TestCase):
    """SMPTE ST 2067-3 resource timing, marker timeline, 2016 schema."""

    def setUp(self):
        self.out = parse(CPL_2016)

    def test_2016_schema_accepted(self):
        self.assertEqual((self.out["title"], self.out["content_kind"]), ("T2016", "trailer"))

    def test_timecode_start(self):
        self.assertEqual(self.out["timecode_start"], "01:00:00:00")

    def test_repeat_count_and_entry_point(self):
        # segment 1 image: 10 units x 5 repeats + (100 - 10) after the entry point
        self.assertEqual(self.out["sequences"]["MainImageSequence"]["duration_units"], "190")

    def test_resource_edit_rate_conversion(self):
        # 48000 Hz audio counted in composition (24) edit units: 140 + 20
        self.assertEqual(self.out["sequences"]["MainAudioSequence"]["duration_units"], "160")

    def test_composition_duration(self):
        # segment lengths follow the main image sequence: 140 + 50
        self.assertEqual((self.out["duration_units"], self.out["duration"]), ("190", "95/12"))

    def test_markers_on_composition_timeline(self):
        # second marker lands after segment 1: 140 + 25
        self.assertEqual([(m["label"], m["offset"]) for m in self.out["markers"]],
                         [("A", "7"), ("B", "165")])

    def test_track_files_distinct(self):
        self.assertEqual(self.out["track_files"], 4)  # f2 reused in both segments

    def test_tracks_per_sequence_kind(self):
        self.assertEqual(self.out["tracks"],
                         {"MainAudioSequence": 2, "MainImageSequence": 1, "MarkerSequence": 1})

    def test_mismatched_segments(self):
        self.assertEqual(self.out["mismatched_segments"], [2])  # audio 20 vs image 50


if __name__ == "__main__":
    unittest.main()
