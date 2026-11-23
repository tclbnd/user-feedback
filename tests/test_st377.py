"""SMPTE ST 377-1 parsing rules exercised on synthetic files from files.py."""
import unittest
from fractions import Fraction

from mxfkit.mxf import parse

from files import header_metadata, index_segment, mxf, body_partition_mxf, local_set, uid, batch, element


def clip(rate=25, duration=250, video=(1920, 1080, 0, 10), **kw):
    return parse(mxf(header_metadata(rate, duration, video), index=[index_segment(0, duration, rate)], **kw))


class RateAndDurationTests(unittest.TestCase):
    def test_fractional_2997_rate_and_duration(self):
        # 29.97 fps is 30000/1001, not an integer.
        out = clip(Fraction(30000, 1001), 1798)
        self.assertEqual(out["edit_rate"], "30000/1001")
        self.assertEqual(out["duration_edit_units"], 1798)
        self.assertEqual(Fraction(out["duration"]), Fraction(1798 * 1001, 30000))

    def test_integer_rate_stays_compact(self):
        out = clip(50, 1500)
        self.assertEqual(out["edit_rate"], "50")
        self.assertEqual(out["duration"], "30")

    def test_video_sample_rate_is_a_rational(self):
        out = clip(Fraction(30000, 1001), 100)
        self.assertEqual(out["video"][0]["sample_rate"], "30000/1001")

    def test_duration_falls_back_to_descriptor_container_duration(self):
        from files import header_metadata as hm
        # Material picture sequence has no Duration; the CDCI descriptor's
        # Container Duration supplies the figure.
        sets = hm(25, 125, open_picture=True)
        out = parse(mxf(sets))
        self.assertEqual(out["duration_edit_units"], 125)
        self.assertEqual(out["duration"], "5")


class VideoGeometryTests(unittest.TestCase):
    def test_interlaced_frame_height_is_double_field_height(self):
        # 1080i: stored height is the field height (540); frame height is 1080.
        out = clip(25, 100, (1920, 540, 1, 10))
        v = out["video"][0]
        self.assertEqual((v["width"], v["height"]), (1920, 1080))
        self.assertTrue(v["interlaced"])

    def test_layout_four_also_interlaced(self):
        out = clip(25, 100, (1440, 576, 4, 8))
        v = out["video"][0]
        self.assertEqual(v["height"], 1152)
        self.assertTrue(v["interlaced"])

    def test_progressive_height_unchanged(self):
        out = clip(50, 100, (1280, 720, 0, 8))
        v = out["video"][0]
        self.assertEqual((v["width"], v["height"]), (1280, 720))
        self.assertFalse(v["interlaced"])


class AudioTests(unittest.TestCase):
    def test_sound_descriptor_sample_rate_is_rational(self):
        out = clip(25, 100)
        self.assertEqual(out["audio"][0]["sample_rate"], "48000")
        self.assertEqual(out["audio"][0]["channels"], 2)
        self.assertEqual(out["audio"][0]["bits"], 24)

    def test_sound_essence_items_classified(self):
        out = clip(25, 100, audio=2, frames=100)
        self.assertEqual(out["essence"]["sound"], 200)
        self.assertNotIn("other", out["essence"])
        # Value bytes only: 100 frames * 2 tracks * 96 bytes, no KLV overhead.
        self.assertEqual(out["essence_bytes"]["sound"], 100 * 2 * 96)

    def test_all_item_types(self):
        extra = element("data", 1, 40) + element("compound", 1, 60)
        out = parse(mxf(header_metadata(25, 10), frames=3, extra=extra))
        self.assertEqual(out["essence"]["data"], 1)
        self.assertEqual(out["essence"]["compound"], 1)
        self.assertEqual(out["essence_bytes"]["data"], 40)
        self.assertEqual(out["essence_bytes"]["compound"], 60)


class PartitionTests(unittest.TestCase):
    def test_operational_patterns(self):
        for name, want in (("OP1a", "OP1a"), ("OP1b", "OP1b"), ("OP2a", "OP2a"),
                           ("OP3c", "OP3c"), ("OP-Atom", "OP-Atom")):
            out = parse(mxf(header_metadata(25, 10), op=name))
            self.assertEqual(out["operational_pattern"], want, name)

    def test_partition_status_flags(self):
        # ST 377-1 partition status: 1 open/incomplete, 2 closed/incomplete,
        # 3 open/complete, 4 closed/complete.
        out = parse(mxf(header_metadata(25, 10), header_status=1, footer_status=3))
        header, footer = out["partitions"]
        self.assertEqual((header["closed"], header["complete"]), (False, False))
        self.assertEqual((footer["closed"], footer["complete"]), (False, True))

    def test_body_sid_from_standard_pack(self):
        out = parse(mxf(header_metadata(25, 10), body_sid=7))
        self.assertEqual(out["partitions"][0]["body_sid"], 7)

    def test_run_in_is_counted(self):
        out = parse(mxf(header_metadata(25, 10), run_in=bytes(range(16))))
        self.assertEqual(out["run_in"], 16)


class HeaderMetadataSelectionTests(unittest.TestCase):
    def test_closed_complete_partition_preferred(self):
        # Header copy is open/incomplete; body copy is closed and complete.
        good = header_metadata(25, 100, (1280, 720, 0, 8))
        bad = header_metadata(25, 50, (640, 360, 0, 8))
        out = parse(body_partition_mxf(bad, frames=2, header_status=1, body_status=4,
                                       body_sets=good))
        self.assertEqual(out["video"][0]["width"], 1280)
        self.assertEqual(out["duration_edit_units"], 100)

    def test_best_of_two_open_partitions(self):
        # Neither partition is closed: any available copy may be used, but the
        # result must still parse and not raise.
        sets = header_metadata(25, 40)
        out = parse(body_partition_mxf(sets, frames=2, header_status=1, body_status=1,
                                       body_sets=sets))
        self.assertEqual(out["duration_edit_units"], 40)

    def test_incomplete_footer_beats_no_metadata(self):
        sets = header_metadata(25, 33)
        out = parse(mxf(sets, header_status=1, footer_status=1,
                        footer_sets=sets))
        self.assertEqual(out["duration_edit_units"], 33)


class IndexTests(unittest.TestCase):
    def test_duplicate_segments_counted_once(self):
        seg = index_segment(0, 250, 25, uid_=uid(900))
        # Same Instance UID repeated in body and footer: one distinct segment.
        out = parse(body_partition_mxf(header_metadata(25, 10), frames=2,
                                       body_index=[seg], index=[seg]))
        self.assertEqual(out["index"]["segments"], 1)
        self.assertEqual(out["index"]["edit_units"], 250)

    def test_distinct_segments_both_counted(self):
        s1 = index_segment(0, 100, 25, uid_=uid(901))
        s2 = index_segment(100, 50, 25, uid_=uid(902))
        out = parse(body_partition_mxf(header_metadata(25, 10), frames=2,
                                       body_index=[s1], index=[s2]))
        self.assertEqual(out["index"]["segments"], 2)
        self.assertEqual(out["index"]["edit_units"], 150)

    def test_constant_bytes_single_value(self):
        seg = index_segment(0, 250, 25, cbe=1234, uid_=uid(903))
        out = parse(mxf(header_metadata(25, 10), index=[seg]))
        self.assertEqual(out["index"]["constant_bytes"], 1234)

    def test_constant_bytes_conflicting_is_null(self):
        s1 = index_segment(0, 100, 25, cbe=100, uid_=uid(904))
        s2 = index_segment(100, 50, 25, cbe=200, uid_=uid(905))
        out = parse(body_partition_mxf(header_metadata(25, 10), frames=2,
                                       body_index=[s1], index=[s2]))
        self.assertIsNone(out["index"]["constant_bytes"])

    def test_index_sid_attributed_to_partition(self):
        seg = index_segment(0, 100, 25, index_sid=3, uid_=uid(906))
        out = parse(mxf(header_metadata(25, 10), index=[seg]))
        self.assertEqual(out["partitions"][0]["index_sid"], 0)
        self.assertEqual(out["partitions"][1]["index_sid"], 3)


class TimecodeTests(unittest.TestCase):
    def _with_timecode(self, frames, base, drop):
        import files
        from mxfkit.mxf import _u
        sets = files.header_metadata(base, 100)
        trk, tc_sets = files.timecode_track(frames, base, drop)
        sets += tc_sets
        # Replace the original material package in place, adding the TC track.
        mp_tracks = _material_track_refs(sets) + [trk]
        new_mp = files.local_set(0x3600, [(0x3C0A, files.uid(777)),
                                          (0x4403, files.batch(mp_tracks))])
        for i, item in enumerate(sets):
            if _u(item[13:15]) == 0x3600:
                sets[i] = new_mp
                break
        return parse(files.mxf(sets))

    def test_non_drop_timecode(self):
        out = self._with_timecode(3661, Fraction(25, 1), False)
        self.assertEqual(out["start_timecode"], "00:02:26:11")

    def test_drop_frame_uses_semicolon(self):
        out = self._with_timecode(1800, Fraction(30000, 1001), True)
        self.assertEqual(out["start_timecode"][8], ";")


class FillTests(unittest.TestCase):
    def test_fill_version_byte_varies(self):
        out = parse(mxf(header_metadata(25, 10), fill_version=3))
        self.assertEqual(out["fill_bytes"], 20)


class RobustnessTests(unittest.TestCase):
    def test_run_in_with_klv_content(self):
        from files import klv
        junk = klv(b"\x06\x0e\x2b\x34\x01\x02\x01\x01\x0d\x01\x03\x01\x13\x01\x01\x01", b"junk")
        out = parse(mxf(header_metadata(25, 10), run_in=junk))
        self.assertEqual(out["run_in"], len(junk))

    def test_unknown_essence_item_stays_other(self):
        from files import klv, H
        weird = klv(H("060e2b34010201010d010301") + bytes([0x19, 1, 1, 1]), b"x" * 12)
        out = parse(mxf(header_metadata(25, 10), extra=weird))
        self.assertEqual(out["essence"]["other"], 1)
        self.assertEqual(out["essence_bytes"]["other"], 12)

    def test_no_metadata_sets(self):
        # A partition with no metadata: numeric fields degrade to null.
        from files import partition, rip as build_rip
        blob = partition("header") + partition("footer") + build_rip(2)
        out = parse(blob)
        self.assertIsNone(out["edit_rate"])
        self.assertIsNone(out["duration"])
        self.assertEqual(out["video"], [])

    def test_missing_header_raises(self):
        from mxfkit.mxf import MxfError
        with self.assertRaises(MxfError):
            parse(b"not an mxf file at all" * 4)


def _material_track_refs(sets):
    """UIDs referenced by tag 0x4403 of the material package set (0x3600)."""
    from mxfkit.mxf import _refs, _u, ber
    for item in sets:
        if _u(item[13:15]) == 0x3600:
            _, value_start = ber(item, 16)
            return _refs(_local_value(item, value_start, 0x4403, _u))
    return []


def _local_value(item, value_start, tag, u):
    body = item[value_start:]
    pos = 0
    while pos + 4 <= len(body):
        t, n = u(body[pos:pos + 2]), u(body[pos + 2:pos + 4])
        if t == tag:
            return body[pos + 4:pos + 4 + n]
        pos += 4 + n
    raise KeyError(hex(tag))


if __name__ == "__main__":
    unittest.main()
