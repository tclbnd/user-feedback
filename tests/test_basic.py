import json
import os
import unittest

from midiclock import scoring
from midiclock.timing import analyse

EXAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "examples", "harbour.mid")


def smf(*tracks, division=480):
    head = b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big") + len(tracks).to_bytes(2, "big") + division.to_bytes(2, "big")
    return head + b"".join(b"MTrk" + len(t).to_bytes(4, "big") + t for t in tracks)


class BasicTests(unittest.TestCase):
    def test_one_note(self):
        t = bytes([0x00, 0x90, 60, 100, 0x83, 0x60, 0x80, 60, 0, 0x00, 0xFF, 0x2F, 0x00])
        out = analyse(smf(t))
        self.assertEqual([(n["pitch"], n["start"], n["end"]) for n in out["notes"]], [(60, 0.0, 0.5)])

    def test_score(self):
        notes = [{"start": 0, "end": 1, "pitch": 60}]
        self.assertEqual(scoring.score(notes, [(0.1, 72), (0.5, 61), (0.9, None)]), 0.6667)


class ScoringTests(unittest.TestCase):
    def test_octave_equivalent_pitch_counts(self):
        notes = [{"start": 0.0, "end": 1.0, "pitch": 60}]
        self.assertEqual(scoring.score(notes, [(0.5, 72), (0.5, 48)]), 1.0)

    def test_wrong_pitch_misses(self):
        notes = [{"start": 0.0, "end": 1.0, "pitch": 60}]
        self.assertEqual(scoring.score(notes, [(0.5, 62)]), 0.0)

    def test_tolerance_widens_the_window(self):
        notes = [{"start": 0.0, "end": 1.0, "pitch": 60}]
        self.assertEqual(scoring.score(notes, [(0.5, 62)], tolerance=2.0), 1.0)

    def test_frames_outside_notes_are_skipped(self):
        notes = [{"start": 0.0, "end": 1.0, "pitch": 60}]
        self.assertEqual(scoring.score(notes, [(2.0, 62), (0.5, 60)]), 1.0)

    def test_empty_pitch_track(self):
        self.assertEqual(scoring.score([{"start": 0, "end": 1, "pitch": 60}], []), 0.0)

    def test_overlapping_notes_prefer_the_one_started_last(self):
        notes = [{"start": 0.0, "end": 2.0, "pitch": 60},
                 {"start": 1.0, "end": 1.5, "pitch": 67}]
        self.assertEqual(scoring.note_at(notes, 1.25)["pitch"], 67)
        self.assertEqual(scoring.note_at(notes, 1.75)["pitch"], 60)
        self.assertIsNone(scoring.note_at(notes, 0.5 + 2.0))

    def test_note_end_is_exclusive(self):
        notes = [{"start": 0.0, "end": 1.0, "pitch": 60}]
        self.assertIsNotNone(scoring.note_at(notes, 0.999))
        self.assertIsNone(scoring.note_at(notes, 1.0))

    def test_percussion_notes_are_not_scored(self):
        notes = [{"start": 0.0, "end": 1.0, "pitch": 38, "channel": 9},
                 {"start": 0.0, "end": 1.0, "pitch": 60, "channel": 0}]
        self.assertEqual(scoring.note_at(notes, 0.5)["channel"], 0)
        only_drums = [{"start": 0.0, "end": 1.0, "pitch": 38, "channel": 9}]
        self.assertIsNone(scoring.note_at(only_drums, 0.5))


class ExampleFileTests(unittest.TestCase):
    def setUp(self):
        with open(EXAMPLE, "rb") as fh:
            self.out = analyse(fh.read())

    def test_shape(self):
        self.assertEqual((self.out["format"], self.out["division"], self.out["tracks"]), (1, 480, 3))

    def test_tempo_map_is_global_and_in_seconds(self):
        self.assertEqual([(t["tick"], t["us_per_quarter"], t["seconds"]) for t in self.out["tempo_map"]],
                         [(0, 500000, 0.0), (7680, 461538, 8.0), (15360, 545454, 15.384608)])

    def test_lyrics(self):
        self.assertEqual([l["text"] for l in self.out["lyrics"]],
                         ["Low ", "tide, ", "high ", "hopes, ", "the ", "lan", "tern ", "burns "])
        self.assertEqual([l["seconds"] for l in self.out["lyrics"]],
                         [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])

    def test_notes_are_paired_and_in_seconds(self):
        self.assertEqual(len(self.out["notes"]), 19)
        self.assertTrue(all(n["start"] < n["end"] for n in self.out["notes"]))
        self.assertEqual(self.out["notes"][0],
                         {"track": 1, "channel": 0, "pitch": 64, "velocity": 96,
                          "start": 0.0, "end": 1.0})

    def test_json_serialisable(self):
        json.dumps(self.out)


if __name__ == "__main__":
    unittest.main()
