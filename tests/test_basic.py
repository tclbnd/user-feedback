import unittest

from discidkit.discid import parse

TOC = "TRACK 01 AUDIO 0\nTRACK 02 AUDIO 7500\nLEADOUT 15000\n"

# 12-track audio disc; addresses are LBAs without the 150-sector lead-in
ALBUM = ("TRACK 01 AUDIO 0\nTRACK 02 AUDIO 20345\nTRACK 03 AUDIO 39145\n"
         "TRACK 04 AUDIO 63157\nTRACK 05 AUDIO 79657\nTRACK 06 AUDIO 101644\n"
         "TRACK 07 AUDIO 131654\nTRACK 08 AUDIO 151530\nTRACK 09 AUDIO 177085\n"
         "TRACK 10 AUDIO 194506\nTRACK 11 AUDIO 223306\nTRACK 12 AUDIO 245528\n"
         "LEADOUT 260543\n")

# Enhanced CD: six audio tracks then a data track in a second session
ENHANCED = ("TRACK 01 AUDIO 0\nTRACK 02 AUDIO 15213\nTRACK 03 AUDIO 32164\n"
            "TRACK 04 AUDIO 46442\nTRACK 05 AUDIO 63264\nTRACK 06 AUDIO 80339\n"
            "TRACK 07 DATA 106712\nLEADOUT 148712\n")


class BasicTests(unittest.TestCase):
    def test_counts(self):
        out = parse(TOC)
        self.assertEqual((out["tracks"], out["audio_tracks"], out["data_tracks"]), (2, 2, 0))

    def test_length(self):
        self.assertEqual(parse(TOC)["audio_seconds"], "200")


class CddbTests(unittest.TestCase):
    def test_leadin_counted_in_digit_sum(self):
        # bug 1: the 2-second lead-in must be added before the digit sum,
        # otherwise this disc yields the old, wrong "940d910c"
        self.assertEqual(parse(ALBUM)["cddb_id"], "ac0d910c")

    def test_enhanced_cddb_counts_every_track(self):
        out = parse(ENHANCED)
        self.assertEqual(out["tracks"], 7)
        self.assertEqual(out["cddb_id"], "3f07be07")


class MusicBrainzTests(unittest.TestCase):
    def test_id_shape_is_url_safe_without_padding(self):
        mb = parse(ALBUM)["musicbrainz_id"]
        self.assertEqual(len(mb), 27)
        self.assertNotIn("=", mb)
        self.assertNotIn("+", mb)
        self.assertNotIn("/", mb)

    def test_album_id(self):
        # uppercase hex SHA-1 input and URL-safe base64 (bugs 2 and 3)
        self.assertEqual(parse(ALBUM)["musicbrainz_id"],
                         "t6rBuJVj6soo8hEbdRP6HCWAKgI")

    def test_enhanced_uses_audio_session_only(self):
        # bug 4: the data track and whole-disc lead-out must not take part;
        # the audio session lead-out is 106712 - 11400 = 95312
        out = parse(ENHANCED)
        self.assertEqual(out["audio_tracks"], 6)
        self.assertEqual(out["data_tracks"], 1)
        self.assertEqual(out["audio_seconds"], "95312/75")
        self.assertEqual(out["musicbrainz_id"],
                         "49HHV7Eb8UKF3aQiNmu1GR8vKTY")


class AccurateRipTests(unittest.TestCase):
    def test_offsets_include_leadin(self):
        # bug 5: every offset and the lead-out address include the lead-in
        self.assertEqual(parse(ALBUM)["accuraterip_id"],
                         "012-0019c9c2-00bed35b-ac0d910c")

    def test_first_lba_zero_omits_leadout_in_second_id(self):
        # bug 6: first track at LBA 0 -> second ID has no lead-out term.
        # offsets incl. lead-in: 150, 7650; lead-out 15150.
        # id1 = 150 + 7650 + 15150 = 22950 (0x59a6)
        # id2 = 150*1 + 7650*2       = 15450 (0x3c5a, no lead-out term)
        out = parse(TOC)
        self.assertEqual(out["accuraterip_id"], "002-000059a6-00003c5a-" + out["cddb_id"])

    def test_nonzero_first_lba_keeps_leadout_in_second_id(self):
        out = parse("TRACK 01 AUDIO 300\nTRACK 02 AUDIO 7800\n"
                    "TRACK 03 AUDIO 15300\nLEADOUT 22800\n")
        self.assertEqual(out["accuraterip_id"],
                         "003-0000b6d0-00025b84-" + out["cddb_id"])

    def test_enhanced_uses_audio_session_only(self):
        out = parse(ENHANCED)
        self.assertTrue(out["accuraterip_id"].startswith("006-"))
        self.assertEqual(out["accuraterip_id"],
                         "006-000517d8-0011004e-3f07be07")


if __name__ == "__main__":
    unittest.main()

