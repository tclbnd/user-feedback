import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ass

NBSP = " "
TMP = "/private/tmp/zq-single-pilot-r9r_s6tm/A21/tmp"
os.makedirs(TMP, exist_ok=True)


def spans(text, t=0, start=0, style=None):
    return ass.parse_event(text, style, start, t)


class CleanText(unittest.TestCase):
    def test_plain_text_single_span(self):
        txt, sp = spans("hello world")
        self.assertEqual(txt, "hello world")
        self.assertEqual(len(sp), 1)
        self.assertEqual(sp[0]["text"], "hello world")
        self.assertIsNone(sp[0]["kara"])
        self.assertEqual(sp[0]["style"], {"i": 0, "b": 0, "u": 0})

    def test_unicode_preserved(self):
        txt, sp = spans("竖排テキストABC")
        self.assertEqual(txt, "竖排テキストABC")
        self.assertEqual([c for c in txt], list("竖排テキストABC"))

    def test_missing_style_key_does_not_crash(self):
        txt, sp = spans("x", style=None)
        self.assertEqual(sp[0]["style"], {"i": 0, "b": 0, "u": 0})

    def test_unbalanced_braces_are_literal(self):
        txt, sp = spans("{oops")
        self.assertEqual(txt, "{oops")
        self.assertIsNone(sp[0]["kara"])
        txt2, sp2 = spans("a}b")
        self.assertEqual(txt2, "a}b")


class KaraokeTiming(unittest.TestCase):
    def test_chained_segments_use_their_own_windows(self):
        text = r"{\k10}a{\kf20}b{\ko30}c"
        table = {
            0:    (0.0, 0.0, 0.0),
            50:   (0.5, 0.0, 0.0),
            100:  (1.0, 0.0, 0.0),
            200:  (1.0, 0.5, 0.0),
            300:  (1.0, 1.0, 0.0),
            500:  (1.0, 1.0, 2 / 3),
            9999: (1.0, 1.0, 1.0),
        }
        for t, want in table.items():
            txt, sp = spans(text, t=t)
            self.assertEqual(txt, "abc")
            self.assertEqual([s["text"] for s in sp], ["a", "b", "c"])
            self.assertEqual([s["kara"]["mode"] for s in sp],
                             ["k", "f", "o"])
            got = tuple(s["kara"]["fill"] for s in sp)
            for g, w in zip(got, want):
                self.assertAlmostEqual(g, round(w, 3), places=3)

    def test_progress_after_seek_uses_absolute_time(self):
        # event begins at 5000 ms; seeking back to 6500 must recompute the
        # fraction from the event start (1500/2000 = 0.75), never report a
        # stale full highlight from the sentence-level start time
        txt, sp = spans(r"{\k200}a", t=6500, start=5000)
        self.assertEqual(sp[0]["kara"]["fill"], 0.75)
        # clamping still holds at and beyond the slot, and before the event
        self.assertEqual(spans(r"{\k200}a", t=7000, start=5000)[1][0]
                         ["kara"]["fill"], 1.0)
        self.assertEqual(spans(r"{\k200}a", t=5000, start=5000)[1][0]
                         ["kara"]["fill"], 0.0)

    def test_zero_duration_segments_step_at_boundary(self):
        text = r"{\k0}a{\k10}b{\k0}c"
        # a: [0,0) done at t>=0; b: [0,100); c: zero slot at t=100
        txt, sp = spans(text, t=0)
        self.assertEqual([s["kara"]["fill"] for s in sp], [1.0, 0.0, 0.0])
        txt, sp = spans(text, t=50)
        self.assertEqual([s["kara"]["fill"] for s in sp], [1.0, 0.5, 0.0])
        txt, sp = spans(text, t=500)
        self.assertEqual([s["kara"]["fill"] for s in sp], [1.0, 1.0, 1.0])
        # boundary-tangent sample: c turns on exactly at its 100 ms boundary
        txt, sp = spans(text, t=100)
        self.assertEqual([s["kara"]["fill"] for s in sp], [1.0, 1.0, 1.0])

    def test_zero_duration_between_segments_does_not_advance_window(self):
        # a: 0 ms, b: 100 ms  -> b's window starts at t=0
        txt, sp = spans(r"{\k0}a{\k10}b", t=80)
        self.assertEqual(sp[0]["kara"]["fill"], 1.0)
        self.assertEqual(sp[1]["kara"]["fill"], 0.8)

    def test_fill_rounded_to_three_decimals(self):
        _, sp = spans(r"{\k3}x", t=10)  # 10/30 = 0.3333...
        self.assertEqual(sp[0]["kara"]["fill"], 0.333)

    def test_pending_segment_overwritten_when_no_text_run(self):
        # first tag never receives text; second segment chains off its end
        txt, sp = spans(r"{\k10}{\k20}b", t=150)
        self.assertEqual(txt, "b")
        self.assertAlmostEqual(sp[0]["kara"]["fill"], 0.25, places=3)


class ResetAndStyles(unittest.TestCase):
    def test_reset_cancels_pending_karaoke(self):
        txt, sp = spans(r"{\k10}x{\r}after", t=50)
        self.assertEqual(txt, "xafter")
        self.assertEqual(sp[0]["kara"]["fill"], 0.5)
        self.assertIsNone(sp[1]["kara"])
        self.assertEqual(sp[1]["style"], {"i": 0, "b": 0, "u": 0})

    def test_style_off_tags_are_evaluated(self):
        text = r"{\i1}a{\i0}b{\b1}c{\u1}d{\u0}e"
        _, sp = spans(text)
        flags = [(s["text"], s["style"]) for s in sp]
        self.assertEqual(flags, [
            ("a", {"i": 1, "b": 0, "u": 0}),
            ("b", {"i": 0, "b": 0, "u": 0}),
            ("c", {"i": 0, "b": 1, "u": 0}),
            ("d", {"i": 0, "b": 1, "u": 1}),
            ("e", {"i": 0, "b": 1, "u": 0}),
        ])

    def test_event_base_style_seeds_and_reset_restores(self):
        text = r"{\b0}x{\r}y{\i1}z"
        _, sp = spans(text, style={"i": 1, "b": 1, "u": 0})
        self.assertEqual(sp[0]["text"], "x")
        self.assertEqual(sp[0]["style"], {"i": 1, "b": 0, "u": 0})
        self.assertEqual(sp[1]["text"], "y")
        self.assertEqual(sp[1]["style"], {"i": 1, "b": 1, "u": 0})
        self.assertEqual(sp[2]["style"], {"i": 1, "b": 1, "u": 0})

    def test_reset_nested_with_escapes_and_karaoke(self):
        text = r"{\i1}{\k10}ab\h{\r}{\b1}cd"
        txt, sp = spans(text, t=100)
        self.assertEqual(txt, "ab" + NBSP + "cd")
        self.assertEqual([s["text"] for s in sp],
                         ["ab" + NBSP, "cd"])
        self.assertEqual(sp[0]["kara"]["fill"], 1.0)
        self.assertEqual(sp[0]["style"]["i"], 1)
        # \r restored the event-less base style and killed pending timing
        self.assertIsNone(sp[1]["kara"])
        self.assertEqual(sp[1]["style"], {"i": 0, "b": 1, "u": 0})


class EscapesAndBraces(unittest.TestCase):
    def test_literal_doubled_braces(self):
        txt, sp = spans(r"{{lit}}")
        self.assertEqual(txt, "{lit}")
        self.assertEqual(len(sp), 1)
        self.assertIsNone(sp[0]["kara"])

    def test_nbsp_and_newline_resolve_to_single_chars(self):
        txt, sp = spans(r"a\hb\nc")
        self.assertEqual(txt, "a" + NBSP + "b\nc")
        self.assertEqual(len(txt), 5)

    def test_escapes_inside_karaoke_run_count_as_run_text(self):
        txt, sp = spans(r"{\k10}a\nb", t=50)
        self.assertEqual(txt, "a\nb")
        self.assertEqual(len(sp), 1)
        self.assertEqual(sp[0]["kara"]["fill"], 0.5)

    def test_unknown_braced_tags_dropped_but_text_kept(self):
        txt, sp = spans(r"x{\fad(100,200)}y{\pos(1,2)}z")
        self.assertEqual(txt, "xyz")
        self.assertTrue(all(s["kara"] is None for s in sp))

    def test_adjacent_identical_spans_merge(self):
        # the second {\i1} is still a tag, hence a span boundary; only text
        # tokens with no tag between them can merge into one run
        _, sp = spans(r"ab{\i1}cd{\i1}ef")
        self.assertEqual([s["text"] for s in sp], ["ab", "cd", "ef"])
        self.assertEqual(sp[0]["style"]["i"], 0)
        self.assertEqual(sp[1]["style"]["i"], 1)
        self.assertEqual(sp[2]["style"]["i"], 1)

    def test_plain_text_split_by_unknown_tag_stays_separate(self):
        _, sp = spans(r"ab{\fad(1,2)}cd")
        self.assertEqual([s["text"] for s in sp], ["ab", "cd"])

    def test_span_texts_reconstruct_visible_text_in_order(self):
        text = r"{{ok}} {\k10}ab\nh{\i1}cd{\r}e\hf"
        txt, sp = spans(text, t=30)
        self.assertEqual("".join(s["text"] for s in sp), txt)
        # every visible character belongs to exactly one ordered span
        joined = "".join(s["text"] for s in sp)
        self.assertEqual(len(joined), len(txt))


class SamplingAndLayers(unittest.TestCase):
    def _run(self, events, sample_times):
        ipath = os.path.join(TMP, "in.json")
        opath = os.path.join(TMP, "out.json")
        with open(ipath, "w", encoding="utf-8") as f:
            json.dump({"events": events, "samples": sample_times}, f)
        subprocess.run([sys.executable, ass.__file__, ipath, opath], check=True)
        with open(opath, encoding="utf-8") as f:
            return json.load(f)

    def test_half_open_window_and_boundary_tangency(self):
        events = [
            {"layer": 0, "start": 0, "end": 1000, "text": "first", "style": None},
            {"layer": 0, "start": 1000, "end": 2000, "text": "second", "style": None},
        ]
        out = self._run(events, [0, 999, 1000, 2000])
        visible = [[ln["text"] for ln in s["lines"]] for s in out["samples"]]
        self.assertEqual(visible, [["first"], ["first"], ["second"], []])
        self.assertEqual([s["t"] for s in out["samples"]], [0, 999, 1000, 2000])

    def test_same_layer_sorts_by_start_then_input_index(self):
        events = [
            {"layer": 0, "start": 100, "end": 5000, "text": "late", "style": None},
            {"layer": 0, "start": 0, "end": 5000, "text": "early", "style": None},
            {"layer": 0, "start": 0, "end": 5000, "text": "same", "style": None},
        ]
        out = self._run(events, [1000])
        self.assertEqual([ln["text"] for ln in out["samples"][0]["lines"]],
                         ["early", "same", "late"])

    def test_layers_draw_order_with_multiple_at_same_instant(self):
        events = [
            {"layer": 2, "start": 0, "end": 100, "text": "top", "style": None},
            {"layer": 0, "start": 0, "end": 100, "text": "bottom", "style": None},
            {"layer": 1, "start": 0, "end": 100, "text": "mid", "style": None},
        ]
        out = self._run(events, [50])
        self.assertEqual([ln["layer"] for ln in out["samples"][0]["lines"]],
                         [0, 1, 2])

    def test_variable_frame_rate_sample_grid(self):
        events = [{"layer": 0, "start": 0, "end": 10000,
                   "text": r"{\k100}a{\k100}b", "style": None}]
        # uneven gaps typical of VFR extraction; slots are [0,1000) [1000,2000)
        grid = [0, 333, 1000, 1001, 1500, 1999]
        out = self._run(events, grid)
        self.assertEqual([s["t"] for s in out["samples"]], grid)
        fills = [[sp["kara"]["fill"] for sp in s["lines"][0]["spans"]]
                 for s in out["samples"]]
        # a owns [0,1000), b owns [1000,2000); tangency at 1000 flips a to 1
        self.assertEqual(fills[0], [0.0, 0.0])
        self.assertEqual(fills[1], [0.333, 0.0])
        self.assertEqual(fills[2], [1.0, 0.0])
        self.assertEqual(fills[3], [1.0, 0.001])
        self.assertEqual(fills[4], [1.0, 0.5])
        self.assertEqual(fills[5], [1.0, 0.999])

    def test_end_to_end_structure_and_escapes_in_output_file(self):
        events = [
            {"layer": 0, "start": 0, "end": 500,
             "text": r"{\i1}{{q}}\h{\k5}x", "style": {"b": 1}},
        ]
        out = self._run(events, [25])
        line = out["samples"][0]["lines"][0]
        self.assertEqual(line["text"], "{q}" + NBSP + "x")
        self.assertEqual(line["spans"][0]["style"], {"i": 1, "b": 1, "u": 0})
        self.assertEqual(line["spans"][-1]["kara"],
                         {"mode": "k", "fill": 0.5})


if __name__ == "__main__":
    unittest.main(verbosity=2)
