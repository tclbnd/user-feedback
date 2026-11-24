import unittest

from ttmlkit.ttml import parse

from docs import doc

NTSC = ' ttp:frameRate="30" ttp:frameRateMultiplier="1000 1001"'


def times(out):
    return [(p["begin"], p["end"]) for p in out["paragraphs"]]


class BasicTests(unittest.TestCase):
    def test_clock_times(self):
        out = parse(doc([("00:00:01.500", "00:00:03", "Hello")]))
        self.assertEqual((out["paragraphs"][0]["begin"], out["paragraphs"][0]["end"]), ("3/2", "3"))

    def test_seconds_offset(self):
        self.assertEqual(parse(doc([("2s", "4.25s", "Hi")]))["duration"], "17/4")


class FrameRateTests(unittest.TestCase):
    def test_clock_frames_use_effective_frame_rate(self):
        # 00:00:01:12 at 30000/1001 fps is 1 s + 12 frames, not 1.12 s.
        out = parse(doc([("00:00:01:12", "00:00:04:00", "Hi")], NTSC))
        self.assertEqual(out["frame_rate"], "30000/1001")
        self.assertEqual(times(out), [("3501/2500", "4")])

    def test_frame_offset_uses_effective_frame_rate(self):
        self.assertEqual(times(parse(doc([("0s", "90f", "Hi")], NTSC))), [("0", "3003/1000")])

    def test_ntsc_frames_do_not_drift_early(self):
        # At 29.97 a 1000-frame offset is 1001/30 s, not the 100/3 s a 30 fps reading gives.
        out = parse(doc([("0s", "1000f", "Hi")], NTSC))
        self.assertEqual(out["paragraphs"][0]["end"], "1001/30")

    def test_sub_frames(self):
        out = parse(doc([("00:00:00:00.1", "1s", "Hi")], NTSC + ' ttp:subFrameRate="2"'))
        self.assertEqual(out["paragraphs"][0]["begin"], "1001/60000")

    def test_tick_rate_default_from_frame_rate(self):
        out = parse(doc([("0s", "30000t", "Hi")], ' ttp:frameRate="30"'))
        self.assertEqual(out["tick_rate"], "30")
        self.assertEqual(out["paragraphs"][0]["end"], "1000")

    def test_tick_rate_default_without_frame_rate(self):
        out = parse(doc([("0s", "2t", "Hi")]))
        self.assertEqual(out["tick_rate"], "1")
        self.assertEqual(out["paragraphs"][0]["end"], "2")

    def test_explicit_tick_rate(self):
        out = parse(doc([("0s", "20000000t", "Hi")], ' ttp:tickRate="10000000"'))
        self.assertEqual(out["paragraphs"][0]["end"], "2")


class UnitTests(unittest.TestCase):
    def test_milliseconds(self):
        self.assertEqual(times(parse(doc([("6500ms", "8.5s", "Hi")]))), [("13/2", "17/2")])

    def test_minutes_and_hours(self):
        self.assertEqual(times(parse(doc([("1m", "1h", "Hi")]))), [("60", "3600")])


class TimingHierarchyTests(unittest.TestCase):
    def test_div_begin_offsets_paragraphs(self):
        d = ('<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttp="http://www.w3.org/ns/ttml#parameter">'
             '<body><div begin="10s"><p begin="1s" end="4s">Hi</p></div></body></tt>')
        self.assertEqual(times(parse(d)), [("11", "14")])

    def test_dur_counts_from_own_begin(self):
        d = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><div begin="10s">'
             '<p begin="2s" dur="3s">Hi</p></div></body></tt>')
        self.assertEqual(times(parse(d)), [("12", "15")])

    def test_earlier_of_end_and_dur_wins(self):
        d = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
             '<p begin="1s" end="5s" dur="2s">Hi</p></div></body></tt>')
        self.assertEqual(times(parse(d)), [("1", "3")])

    def test_seq_container_continues_after_previous_sibling(self):
        d = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><div begin="2s" timeContainer="seq">'
             '<p dur="2s">A</p><p dur="3s">B</p></div></body></tt>')
        self.assertEqual(times(parse(d)), [("2", "4"), ("4", "7")])

    def test_seq_sibling_without_duration_takes_no_time(self):
        d = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><div timeContainer="seq">'
             '<p begin="0s">A</p><p dur="1s">B</p></div></body></tt>')
        self.assertEqual(times(parse(d)), [("0", "0"), ("0", "1")])

    def test_child_clipped_to_parent(self):
        d = ('<tt xmlns="http://www.w3.org/ns/ttml"><body end="5s"><div>'
             '<p begin="1s" end="9s">Hi</p></div></body></tt>')
        self.assertEqual(times(parse(d)), [("1", "5")])

    def test_unbounded_paragraph_ends_with_parent(self):
        d = ('<tt xmlns="http://www.w3.org/ns/ttml"><body end="5s"><div>'
             '<p begin="1s">Hi</p></div></body></tt>')
        self.assertEqual(times(parse(d)), [("1", "5")])


class TextTests(unittest.TestCase):
    def test_br_starts_new_line(self):
        out = parse(doc([("0s", "1s", "Previously on<br/>Harbour Lights")]))
        self.assertEqual(out["paragraphs"][0]["lines"], ["Previously on", "Harbour Lights"])

    def test_whitespace_collapses_within_a_line(self):
        out = parse(doc([("0s", "1s", "Where   were\n  you?")]))
        self.assertEqual(out["paragraphs"][0]["lines"], ["Where were you?"])
