import unittest

from discidkit.discid import parse

TOC = "TRACK 01 AUDIO 0\nTRACK 02 AUDIO 7500\nLEADOUT 15000\n"

ENHANCED = ("TRACK 01 AUDIO 0\nTRACK 02 AUDIO 15213\nTRACK 03 AUDIO 32164\n"
            "TRACK 04 AUDIO 46442\nTRACK 05 AUDIO 63264\nTRACK 06 AUDIO 80339\n"
            "TRACK 07 DATA 106712\nLEADOUT 148712\n")


class BasicTests(unittest.TestCase):
    def test_counts(self):
        out = parse(TOC)
        self.assertEqual((out["tracks"], out["audio_tracks"], out["data_tracks"]), (2, 2, 0))

    def test_length(self):
        self.assertEqual(parse(TOC)["audio_seconds"], "200")

    def test_cddb_includes_leadin(self):
        # Track starts must be turned into seconds with the 150-sector
        # lead-in added before taking the digit sums.
        out = parse(TOC)
        self.assertEqual(out["cddb_id"], "0500c802")

    def test_musicbrainz_alphabet(self):
        out = parse(TOC)
        self.assertEqual(len(out["musicbrainz_id"]), 28)
        self.assertNotIn("+", out["musicbrainz_id"])
        self.assertNotIn("/", out["musicbrainz_id"])
        self.assertNotIn("=", out["musicbrainz_id"])

    def test_accuraterip_absolute_offsets(self):
        # LBAs do not include the lead-in; the AccurateRip offsets do, so a
        # first track at sector 0 still contributes to the second ID.
        out = parse(TOC)
        self.assertEqual(out["accuraterip_id"], "002-000059a6-0000ede4-0500c802")


class EnhancedTests(unittest.TestCase):
    def test_audio_session_split(self):
        out = parse(ENHANCED)
        self.assertEqual((out["tracks"], out["audio_tracks"], out["data_tracks"]), (7, 6, 1))
        # 106712 (first data track) - 11400 (session gap) = 95312
        self.assertEqual(out["audio_seconds"], "95312/75")

    def test_musicbrainz_and_accuraterip_ignore_data_session(self):
        out = parse(ENHANCED)
        self.assertEqual(out["accuraterip_id"].split("-", 1)[0], "006")
        self.assertEqual(len(out["musicbrainz_id"]), 28)
        self.assertNotIn("+", out["musicbrainz_id"])
        self.assertNotIn("/", out["musicbrainz_id"])
        # The FreeDB ID still counts all seven tracks.
        self.assertEqual(int(out["cddb_id"], 16) & 0xFF, 7)


if __name__ == "__main__":
    unittest.main()
