import struct
import unittest

from bnkkit.bnk import BnkError, parse

import banks
from banks import action, bank, bkhd, chunk, event, hirc, media, music_track, obj, set_endian, sound, stid


class VersionLayoutTests(unittest.TestCase):
    def test_legacy_sound_layout_v88(self):
        # <= v88: stream type is a full u32; action count is a u32.
        objs = [sound(11, 101, 0, 88), sound(12, 102, 1, 88), sound(13, 999, 2, 88),
                action(21, 11), event(31, [21], 88)]
        out = parse(bank(version=88, objs=objs, wems=((101, 5000),)))
        self.assertEqual(out["objects"], {"action": 1, "event": 1, "sound": 3})
        self.assertEqual(out["sounds"], {"embedded": 1, "prefetch": 1, "streamed": 1})
        self.assertEqual(out["events"], [{"id": 31, "actions": [21]}])
        # 999 is streamed so its media is not required; prefetch 102 is missing.
        self.assertEqual(out["missing_media"], [102])

    def test_modern_sound_layout_v89(self):
        # v89 is the first version with the one-byte stream type.
        objs = [sound(11, 101, 0, 89), sound(12, 102, 1, 89), sound(13, 999, 2, 89),
                action(21, 11), event(31, [21], 89)]
        out = parse(bank(version=89, objs=objs, wems=((101, 5000),)))
        self.assertEqual(out["sounds"], {"embedded": 1, "prefetch": 1, "streamed": 1})
        self.assertEqual(out["events"], [{"id": 31, "actions": [21]}])
        self.assertEqual(out["missing_media"], [102])

    def test_event_count_width_changes_at_v122(self):
        out_old = parse(bank(version=121, objs=[event(31, [21, 22], 121)]))
        self.assertEqual(out_old["events"], [{"id": 31, "actions": [21, 22]}])
        out_new = parse(bank(version=122, objs=[event(31, [21, 22], 122)]))
        self.assertEqual(out_new["events"], [{"id": 31, "actions": [21, 22]}])

    def test_multiple_objects_do_not_drift(self):
        # The old stride (9 + osize) overshot each object by four bytes, so a
        # bank with several objects failed while a single-object bank worked.
        objs = [sound(i, 101, 0, 134) for i in range(1, 6)]
        out = parse(bank(objs=objs, wems=((101, 16),)))
        self.assertEqual(out["objects"], {"sound": 5})
        self.assertEqual(out["sounds"], {"embedded": 5})
        self.assertFalse(out["missing_media"])


class MediaTests(unittest.TestCase):
    def test_didx_offsets_are_relative_to_data_payload(self):
        didx, data_chunk = media([(5, 100), (6, 200)])
        out = parse(bkhd() + didx + data_chunk)
        self.assertFalse(out["media_outside_data"])
        self.assertEqual([m["offset"] for m in out["media"]], [0, 112])

    def test_outside_data_is_detected(self):
        didx = chunk("DIDX", struct.pack("<III", 5, 4096, 16))
        data_chunk = chunk("DATA", bytes(32))
        out = parse(bkhd() + didx + data_chunk)
        self.assertTrue(out["media_outside_data"])

    def test_missing_media_from_music_track(self):
        objs = [music_track(41, 777, 0), sound(11, 101, 0)]
        out = parse(bank(objs=objs, wems=((101, 16),)))
        self.assertEqual(out["objects"]["music_track"], 1)
        # Music tracks do not count as sounds, but their embedded media is required.
        self.assertNotIn("music_track", out["sounds"])
        self.assertEqual(out["missing_media"], [777])

    def test_missing_media_deduped_and_sorted(self):
        objs = [sound(11, 500, 0), sound(12, 500, 1), sound(13, 300, 1),
                music_track(41, 400, 0)]
        out = parse(bank(objs=objs, wems=()))
        self.assertEqual(out["missing_media"], [300, 400, 500])
        self.assertEqual(out["sounds"], {"embedded": 1, "prefetch": 2})


class StidTests(unittest.TestCase):
    def test_bank_name_matches_bank_id_not_first_entry(self):
        # The first STID row names a different object; the bank must pick the
        # row whose id equals its BKHD bank id.
        body = bank(version=134, names=[(1, "Init"), (0x1234ABCD, "RealName")])
        out = parse(body)
        self.assertEqual(out["bank_name"], "RealName")

    def test_missing_bank_row_leaves_name_none(self):
        out = parse(bank(names=[(999, "Other")]))
        self.assertIsNone(out["bank_name"])


class BigEndianTests(unittest.TestCase):
    def tearDown(self):
        set_endian("<")

    def test_big_endian_bank(self):
        set_endian(">")
        objs = [sound(11, 101, 0, 134), sound(12, 102, 1, 134),
                action(21, 11), event(31, [21], 134)]
        out = parse(bank(version=134, objs=objs, wems=((101, 5000),),
                         names=[(0x1234ABCD, "Console")]))
        self.assertEqual(out["endian"], "big")
        self.assertEqual(out["bank_name"], "Console")
        self.assertEqual(out["sounds"], {"embedded": 1, "prefetch": 1})
        self.assertEqual(out["events"], [{"id": 31, "actions": [21]}])
        self.assertEqual(out["missing_media"], [102])

    def test_big_endian_legacy_layout(self):
        set_endian(">")
        objs = [sound(11, 101, 0, 72), action(21, 11), event(31, [21], 72)]
        out = parse(bank(version=72, objs=objs))
        self.assertEqual(out["endian"], "big")
        self.assertEqual(out["sounds"], {"embedded": 1})


class ErrorTests(unittest.TestCase):
    def test_not_a_bank(self):
        with self.assertRaises(BnkError):
            parse(b"RIFF" + bytes(20))

    def test_truncated_chunk(self):
        good = bank()
        with self.assertRaises(BnkError):
            parse(good[:-5])

    def test_unknown_stream_type_is_named_generically(self):
        # A source type the parser does not know still yields a stable name.
        body = struct.pack("<IBI", 0x00040001, 7, 101) + bytes(14)
        out = parse(bank(objs=[obj(2, 11, body)], wems=()))
        self.assertEqual(out["sounds"], {"type_7": 1})
