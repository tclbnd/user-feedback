"""SMPTE ST 2067-3 behaviour: resources, rates, repeats, markers, timecode, namespaces."""
import unittest

from cplkit.cpl import CplError, parse

NS13 = "http://www.smpte-ra.org/schemas/2067-3/2013"
CC13 = "http://www.smpte-ra.org/schemas/2067-2/2013"
NS16 = "http://www.smpte-ra.org/schemas/2067-3/2016"
CC16 = "http://www.smpte-ra.org/schemas/2067-2/2016"


def res(intrinsic, entry=None, source=None, repeat=None, own_rate=None, track="tf"):
    opts = ""
    if own_rate is not None:
        opts += "<EditRate>%s</EditRate>" % own_rate
    if entry is not None:
        opts += "<EntryPoint>%d</EntryPoint>" % entry
    if source is not None:
        opts += "<SourceDuration>%d</SourceDuration>" % source
    if repeat is not None:
        opts += "<RepeatCount>%d</RepeatCount>" % repeat
    return ("<Resource><IntrinsicDuration>%d</IntrinsicDuration>%s"
            "<TrackFileId>%s</TrackFileId></Resource>") % (intrinsic, opts, track)


def mres(intrinsic, markers, own_rate=None):
    opts = "<EditRate>%s</EditRate>" % own_rate if own_rate is not None else ""
    body = "".join("<Marker><Label>%s</Label><Offset>%d</Offset></Marker>" % m for m in markers)
    return "<Resource><IntrinsicDuration>%d</IntrinsicDuration>%s%s</Resource>" % (intrinsic, opts, body)


def seq(kind, body, track, prefix="cc:"):
    if kind == "MarkerSequence":
        return "<MarkerSequence><TrackId>%s</TrackId><ResourceList>%s</ResourceList></MarkerSequence>" % (track, body)
    return ("<%s%s><TrackId>%s</TrackId><ResourceList>%s</ResourceList></%s%s>"
            % (prefix, kind, track, body, prefix, kind))


def playlist(segments, ns=NS13, cc=CC13, edit_rate="24 1", tc="", kind="", extra_ns=""):
    decl = ' xmlns:cc="%s"%s' % (cc, extra_ns)
    kind_xml = "<ContentKind>%s</ContentKind>" % kind if kind else ""
    return ('<CompositionPlaylist xmlns="{0}"{1}><ContentTitle>T</ContentTitle>{2}'
            "<EditRate>{3}</EditRate>{4}<SegmentList>{5}</SegmentList>"
            "</CompositionPlaylist>").format(ns, decl, kind_xml, edit_rate, tc, "".join(segments)).encode()


def segment(*sequences):
    return "<Segment><SequenceList>%s</SequenceList></Segment>" % "".join(sequences)


def timecode(rate_, drop, start):
    return ("<CompositionTimecode><TimecodeDropFrame>%s</TimecodeDropFrame>"
            "<TimecodeRate>%d</TimecodeRate><TimecodeStartAddress>%s</TimecodeStartAddress>"
            "</CompositionTimecode>") % ("true" if drop else "false", rate_, start)


class St2067Tests(unittest.TestCase):
    def test_2016_namespace_accepted(self):
        out = parse(playlist([segment(seq("MainImageSequence", res(240), "v"))], ns=NS16, cc=CC16))
        self.assertEqual((out["duration_units"], out["duration"]), ("240", "10"))

    def test_unknown_namespace_rejected(self):
        data = playlist([segment(seq("MainImageSequence", res(24), "v"))], ns="urn:bogus", cc="urn:boguscc")
        with self.assertRaises(CplError):
            parse(data)
        with self.assertRaises(CplError):
            parse(b"<NotAPlaylist/>")

    def test_entrypoint_without_source_duration_uses_remainder(self):
        out = parse(playlist([segment(seq("MainImageSequence", res(1000, entry=100), "v"))]))
        self.assertEqual(out["duration_units"], "900")
        out = parse(playlist([segment(seq("MainImageSequence", res(1000, entry=100, source=500), "v"))]))
        self.assertEqual(out["duration_units"], "500")

    def test_repeat_count_multiplies_duration(self):
        out = parse(playlist([segment(seq("MainImageSequence", res(120, repeat=5), "v"))]))
        self.assertEqual((out["duration_units"], out["duration"]), ("600", "25"))

    def test_resource_edit_rate_converted_to_composition_rate(self):
        audio = seq("MainAudioSequence", res(48000, repeat=2, own_rate="48000 1", track="a"), "au")
        image = seq("MainImageSequence", res(48, track="v"), "vi")
        out = parse(playlist([segment(image, audio)]))
        self.assertEqual(out["sequences"]["MainAudioSequence"]["duration_units"], "48")
        self.assertEqual(out["sequences"]["MainImageSequence"]["duration_units"], "48")
        self.assertEqual(out["mismatched_segments"], [])

    def test_sequences_counted_by_element_name_any_namespace(self):
        weird = segment(seq("MainImageSequence", res(36), "v", prefix="zz:"))
        out = parse(playlist([weird], extra_ns=' xmlns:zz="urn:vendor:weird"'))
        self.assertIn("MainImageSequence", out["sequences"])
        self.assertEqual(out["sequences"]["MainImageSequence"]["duration_units"], "36")
        self.assertEqual(out["tracks"]["MainImageSequence"], 1)

    def test_markers_placed_on_composition_timeline(self):
        seg1 = segment(seq("MainImageSequence", res(100, track="v"), "v"),
                       seq("MarkerSequence", mres(100, [("M1", 10)]), "m"))
        seg2 = segment(seq("MainImageSequence", res(50, track="v"), "v"),
                       seq("MarkerSequence", mres(50, [("M2", 5)]), "m"))
        out = parse(playlist([seg1, seg2], tc=timecode(24, False, "01:00:00:00")))
        offsets = [(m["label"], m["offset"], m["timecode"]) for m in out["markers"]]
        self.assertEqual(offsets, [("M1", "10", "01:00:00:10"), ("M2", "105", "01:00:04:09")])
        self.assertEqual(out["timecode_start"], "01:00:00:00")

    def test_marker_offset_uses_resource_edit_rate(self):
        seg = segment(seq("MainImageSequence", res(24, track="v"), "v"),
                      seq("MarkerSequence", mres(48000, [("M", 48000)], own_rate="48000 1"), "m"))
        out = parse(playlist([seg]))
        self.assertEqual(out["markers"][0]["offset"], "24")
        self.assertIsNone(out["markers"][0]["timecode"])

    def test_drop_frame_timecode(self):
        seg = segment(seq("MainImageSequence", res(1800, track="v"), "v"),
                      seq("MarkerSequence", mres(1800, [("M", 1798)]), "m"))
        out = parse(playlist([seg], edit_rate="30 1", tc=timecode(30, True, "01:00:00;00")))
        self.assertEqual(out["markers"][0]["timecode"], "01:01:00;00")

    def test_content_kind_and_distinct_tracks(self):
        seg1 = segment(seq("MainImageSequence", res(24, track="v"), "V1"),
                       seq("MainAudioSequence", res(48000, own_rate="48000 1", track="a1"), "A1"),
                       seq("MarkerSequence", mres(24, []), "M"))
        seg2 = segment(seq("MainImageSequence", res(24, track="v"), "V1"),
                       seq("MainAudioSequence", res(48000, own_rate="48000 1", track="a2"), "A2"),
                       seq("MarkerSequence", mres(24, []), "M"))
        out = parse(playlist([seg1, seg2], kind="feature"))
        self.assertEqual(out["content_kind"], "feature")
        self.assertEqual(out["tracks"],
                         dict(MainAudioSequence=2, MainImageSequence=1, MarkerSequence=1))

    def test_mismatched_segments_reported(self):
        seg1 = segment(seq("MainImageSequence", res(100, track="v"), "v"),
                       seq("MainAudioSequence", res(200000, own_rate="48000 1", track="a"), "a"))
        seg2 = segment(seq("MainImageSequence", res(50, track="v"), "v"),
                       seq("MainAudioSequence", res(96000, own_rate="48000 1", track="a"), "a"))
        out = parse(playlist([seg1, seg2]))
        self.assertEqual(out["mismatched_segments"], [2])

    def test_distinct_track_files_and_resource_counts(self):
        image = seq("MainImageSequence", res(24, track="dup") + res(24, track="dup"), "v")
        audio = seq("MainAudioSequence", res(48000, own_rate="48000 1", track="a"), "au")
        out = parse(playlist([segment(image, audio)]))
        self.assertEqual(out["track_files"], 2)
        self.assertEqual(out["sequences"]["MainImageSequence"]["resources"], 2)

    def test_fraction_duration_string(self):
        out = parse(playlist([segment(seq("MainImageSequence", res(12), "v"))]))
        self.assertEqual(out["duration"], "1/2")


if __name__ == "__main__":
    unittest.main()
