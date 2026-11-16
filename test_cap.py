#!/usr/bin/env python3
"""Acceptance tests for cap.py against the README.md 608 subset.

Cases are fed through the decoder in-process and compared per frame: exact
text, row position, and the mode-change history.
"""
import unittest

from cap import Decoder


def run(events):
    dec = Decoder()
    for ev in events:
        dec.feed(ev)
    return dec


def ctrl(hi, lo=0x14):
    return [lo, hi]


def text(s):
    """Split ASCII into 608 pairs; an odd trailing byte rides as lo, hi=0."""
    pairs = [[ord(s[i]), ord(s[i + 1])] for i in range(0, len(s) - 1, 2)]
    if len(s) % 2:
        pairs.append([ord(s[-1]), 0])
    return pairs


def ev(t, *pairs):
    return {"t": t, "data": list(pairs)}


def screens(dec):
    return [s["screen"] for s in dec.snapshots]


class RollUpTests(unittest.TestCase):
    def test_ru2_scrolls_two_rows(self):
        dec = run([
            ev(0, ctrl(0x25)),
            ev(100, *text("ONE")),
            ev(200, ctrl(0x2D)),
            ev(300, *text("TWO")),
            ev(400, ctrl(0x2D)),
            ev(500, *text("THR")),
        ])
        self.assertEqual(screens(dec)[3], ["", "", "ONE", "TWO"])
        self.assertEqual(screens(dec)[5], ["", "", "TWO", "THR"])

    def test_ru2_oldest_line_drops(self):
        dec = run([
            ev(0, ctrl(0x25)),
            ev(100, *text("L1")),
            ev(200, ctrl(0x2D)),
            ev(300, *text("L2")),
            ev(400, ctrl(0x2D)),
            ev(500, *text("L3")),
        ])
        self.assertEqual(screens(dec)[-1], ["", "", "L2", "L3"])

    def test_ru3_keeps_three_lines(self):
        dec = run([
            ev(0, ctrl(0x25, 0x1C)),
            ev(100, *text("L1")),
            ev(200, ctrl(0x2D)),
            ev(300, *text("L2")),
            ev(400, ctrl(0x2D)),
            ev(500, *text("L3")),
        ])
        self.assertEqual(screens(dec)[-1], ["", "L1", "L2", "L3"])

    def test_ru3_fourth_line_drops_oldest(self):
        dec = run([
            ev(0, ctrl(0x25, 0x1C)),
            ev(100, *text("L1")),
            ev(200, ctrl(0x2D)),
            ev(300, *text("L2")),
            ev(400, ctrl(0x2D)),
            ev(500, *text("L3")),
            ev(600, ctrl(0x2D)),
            ev(700, *text("L4")),
        ])
        self.assertEqual(screens(dec)[-1], ["", "L2", "L3", "L4"])

    def test_ru2_to_ru3_does_not_stack_previous_sentence(self):
        """The reported bug: switching 2-row to 3-row must not stack a line."""
        dec = run([
            ev(0, ctrl(0x25)),            # RU2
            ev(100, *text("OLD1")),
            ev(200, ctrl(0x2D)),
            ev(300, *text("OLD2")),
            ev(400, ctrl(0x25, 0x1C)),    # RU3
            ev(500, ctrl(0x2D)),
            ev(600, *text("NEW1")),
        ])
        self.assertEqual(screens(dec)[-1], ["", "OLD1", "OLD2", "NEW1"])

    def test_ru2_to_ru3_does_not_resurrect_a_dropped_line(self):
        """Exact reported symptom: the old sentence must not reappear."""
        dec = run([
            ev(0, ctrl(0x25)),            # RU2
            ev(100, *text("OLD1")),
            ev(200, ctrl(0x2D)),
            ev(300, *text("OLD2")),
            ev(400, ctrl(0x2D)),
            ev(500, *text("OLD3")),       # OLD1 already scrolled out
            ev(600, ctrl(0x25, 0x1C)),    # RU3 window grows
            ev(700, ctrl(0x2D)),
            ev(800, *text("NEW1")),
        ])
        self.assertEqual(screens(dec)[5], ["", "", "OLD2", "OLD3"])
        self.assertEqual(screens(dec)[-1], ["", "OLD2", "OLD3", "NEW1"])

    def test_ru2_repeat_mid_session_keeps_lines(self):
        """A non-adjacent RU2 restatement must not restart the session."""
        dec = run([
            ev(0, ctrl(0x25)),
            ev(100, *text("L1")),
            ev(200, ctrl(0x2D)),
            ev(300, *text("L2")),
            ev(400, ctrl(0x25)),          # restate RU2, same size
            ev(500, ctrl(0x2D)),
            ev(600, *text("L3")),
        ])
        self.assertEqual(screens(dec)[-1], ["", "", "L2", "L3"])

    def test_text_appends_without_cr(self):
        """The README appends to the current row; only CR breaks the line."""
        dec = run([
            ev(0, ctrl(0x25)),
            ev(100, *text("AB")),
            ev(200, *text("CD")),
        ])
        self.assertEqual(screens(dec)[-1], ["", "", "", "ABCD"])

    def test_ru3_to_ru2_drops_the_row_leaving_the_window(self):
        dec = run([
            ev(0, ctrl(0x25, 0x1C)),
            ev(100, *text("L1")),
            ev(200, ctrl(0x2D)),
            ev(300, *text("L2")),
            ev(400, ctrl(0x2D)),
            ev(500, *text("L3")),
            ev(600, ctrl(0x25)),          # shrink to RU2
        ])
        self.assertEqual(screens(dec)[-1], ["", "", "L2", "L3"])

    def test_roll_up_entry_keeps_current_row_like_any_mode_change(self):
        """RU does not clear the base row: only EDM/ENM erase memory."""
        dec = run([
            ev(0, *text("POP")),
            ev(100, ctrl(0x2F)),          # publish "POP" on the base row
            ev(200, ctrl(0x25, 0x1C)),    # start a roll-up session
            ev(300, ctrl(0x2D)),          # line break
            ev(400, *text("NEW")),
        ])
        self.assertEqual(screens(dec)[-1], ["", "", "POP", "NEW"])


class PopOnTests(unittest.TestCase):
    def test_load_is_not_visible_until_eoc(self):
        dec = run([
            ev(0, ctrl(0x20)),
            ev(100, *text("HI")),
            ev(200, ctrl(0x2F)),
        ])
        self.assertEqual(screens(dec)[1], ["", "", "", ""])
        self.assertEqual(screens(dec)[2], ["", "", "", "HI"])

    def test_multiline_load_stacks_from_bottom(self):
        dec = run([
            ev(0, *text("ONE")),
            ev(100, ctrl(0x2D)),
            ev(200, *text("TWO")),
            ev(300, ctrl(0x2F)),
        ])
        self.assertEqual(screens(dec)[-1], ["", "", "ONE", "TWO"])

    def test_eoc_swaps_both_memories(self):
        dec = run([
            ev(0, *text("A")),          # nondisp row3
            ev(100, ctrl(0x2F)),        # publish A
            ev(200, ctrl(0x2E)),        # enm clears nondisp
            ev(300, *text("B")),        # load B
            ev(400, ctrl(0x2F)),        # publish B
        ])
        self.assertEqual(screens(dec)[1], ["", "", "", "A"])
        self.assertEqual(screens(dec)[2], ["", "", "", "A"])
        self.assertEqual(screens(dec)[3], ["", "", "", "A"])
        self.assertEqual(screens(dec)[4], ["", "", "", "B"])

    def test_eoc_again_brings_back_the_other_memory(self):
        dec = run([
            ev(0, *text("A")),          # load A into non-display
            ev(100, ctrl(0x2F)),        # publish: disp=A, nondisp=""
            ev(200, ctrl(0x20)),        # RCL breaks the adjacent-repeat run
            ev(300, ctrl(0x2F)),        # swap again: disp="", nondisp=A
        ])
        self.assertEqual(screens(dec)[1], ["", "", "", "A"])
        self.assertEqual(screens(dec)[3], ["", "", "", ""])

    def test_edm_clears_display_only(self):
        dec = run([
            ev(0, *text("A")),
            ev(100, ctrl(0x2F)),        # publish A
            ev(200, ctrl(0x2E)),        # ENM clears the hidden memory only
            ev(300, *text("B")),        # load B into the hidden memory
            ev(400, ctrl(0x2C)),        # EDM clears the display
        ])
        self.assertEqual(screens(dec)[3], ["", "", "", "A"])
        self.assertEqual(screens(dec)[4], ["", "", "", ""])

    def test_cr_in_pop_on_moves_up_without_scrolling_off(self):
        dec = run([
            ev(0, *text("TOP")),
            ev(100, ctrl(0x2D)),
            ev(200, *text("BOT")),
            ev(300, ctrl(0x2F)),
        ])
        self.assertEqual(screens(dec)[-1], ["", "", "TOP", "BOT"])


class PaintOnTests(unittest.TestCase):
    def test_paint_write_is_immediately_visible(self):
        dec = run([
            ev(0, ctrl(0x29)),
            ev(100, *text("LIVE")),
        ])
        self.assertEqual(screens(dec)[-1], ["", "", "", "LIVE"])

    def test_cr_after_paint_moves_up(self):
        dec = run([
            ev(0, ctrl(0x29)),
            ev(100, *text("A")),
            ev(200, ctrl(0x2D)),
            ev(300, *text("B")),
        ])
        self.assertEqual(screens(dec)[-1], ["", "", "A", "B"])


class ControlWordTests(unittest.TestCase):
    def test_repeated_control_pair_counts_once(self):
        dec = run([
            ev(0, ctrl(0x25)),
            ev(100, ctrl(0x25)),        # duplicate, ignored
            ev(200, *text("A")),
            ev(300, ctrl(0x2D)),
            ev(400, ctrl(0x2D)),        # duplicate CR, ignored
            ev(500, *text("B")),
        ])
        self.assertEqual(screens(dec)[-1], ["", "", "A", "B"])

    def test_non_adjacent_repeat_is_not_deduped(self):
        dec = run([
            ev(0, ctrl(0x25)),
            ev(100, *text("A")),
            ev(200, ctrl(0x2D)),
            ev(300, *text("B")),
        ])
        self.assertEqual(screens(dec)[-1], ["", "", "A", "B"])

    def test_different_prefix_is_not_a_duplicate(self):
        dec = run([
            ev(0, ctrl(0x25)),          # RU2
            ev(100, ctrl(0x25, 0x1C)),  # RU3, different pair
        ])
        self.assertEqual([m["mode"] for m in dec.modes],
                         ["roll-up"])

    def test_repeated_edm_only_clears_once(self):
        dec = run([
            ev(0, *text("A")),
            ev(100, ctrl(0x2F)),
            ev(200, ctrl(0x2C)),
            ev(300, ctrl(0x2C)),
        ])
        self.assertEqual(screens(dec)[-1], ["", "", "", ""])

    def test_unknown_control_prefix_is_never_text(self):
        """A load/position command must not leak as a visible character."""
        dec = run([
            ev(0, ctrl(0x29)),
            ev(100, [0x14, 0x40]),      # PAC-style command, out of subset
            ev(200, *text("AB")),
        ])
        self.assertEqual(screens(dec)[-1], ["", "", "", "AB"])

    def test_padding_between_repeats_stays_transparent(self):
        dec = run([
            ev(0, ctrl(0x25)),
            ev(100, *text("A")),
            ev(200, ctrl(0x2D)),
            ev(300, [0x80, 0x80]),      # padding is not an action
            ev(400, ctrl(0x2D)),        # still an adjacent repeat: ignored
            ev(500, *text("B")),
        ])
        self.assertEqual(screens(dec)[-1], ["", "", "A", "B"])

    def test_duplicate_control_across_event_boundary_is_one_action(self):
        chunked = run([
            ev(0, ctrl(0x25)),
            ev(100, ctrl(0x2D)),
            ev(200, ctrl(0x2D)),        # adjacent repeat across events
            ev(300, *text("A")),
        ])
        whole = run([
            ev(0, ctrl(0x25), ctrl(0x2D), ctrl(0x2D), *text("A")),
        ])
        self.assertEqual(screens(chunked)[-1], screens(whole)[-1])


class BackspaceTests(unittest.TestCase):
    def test_bs_deletes_last_char(self):
        dec = run([
            ev(0, ctrl(0x29)),
            ev(100, *text("AB")),
            ev(200, ctrl(0x21)),
        ])
        self.assertEqual(screens(dec)[-1], ["", "", "", "A"])

    def test_bs_on_empty_row_is_safe(self):
        dec = run([ev(0, ctrl(0x21))])
        self.assertEqual(screens(dec)[-1], ["", "", "", ""])

    def test_bs_acts_on_non_displayed_memory_in_pop_on(self):
        dec = run([
            ev(0, *text("AB")),
            ev(100, ctrl(0x21)),
            ev(200, ctrl(0x2F)),
        ])
        self.assertEqual(screens(dec)[-1], ["", "", "", "A"])


class AccentTests(unittest.TestCase):
    def test_utf8_accented_text(self):
        # "café" as UTF-8: c a f C3 A9
        dec = run([
            ev(0, ctrl(0x29)),
            ev(100, [ord("c"), ord("a")], [ord("f"), 0xC3], [0xA9, 0]),
        ])
        self.assertEqual(screens(dec)[-1], ["", "", "", "café"])

    def test_latin1_accented_text(self):
        dec = run([
            ev(0, ctrl(0x29)),
            ev(100, [ord("c"), ord("a")], [ord("f"), 0xE9]),
        ])
        self.assertEqual(screens(dec)[-1], ["", "", "", "café"])

    def test_bs_removes_whole_utf8_character(self):
        dec = run([
            ev(0, ctrl(0x29)),
            ev(100, [ord("a"), 0xC3], [0xA9, 0]),
            ev(200, ctrl(0x21)),
        ])
        self.assertEqual(screens(dec)[-1], ["", "", "", "a"])


class SnapshotTests(unittest.TestCase):
    def test_every_event_emits_a_snapshot(self):
        dec = run([
            ev(0, ctrl(0x25)),
            ev(33, *text("A")),
            ev(66, *text("B")),
        ])
        self.assertEqual([s["t"] for s in dec.snapshots], [0, 33, 66])

    def test_row_position_and_state_are_per_frame(self):
        dec = run([
            ev(0, ctrl(0x29)),
            ev(100, *text("A")),
            ev(200, ctrl(0x2C)),
        ])
        self.assertEqual(screens(dec), [
            ["", "", "", ""],
            ["", "", "", "A"],
            ["", "", "", ""],
        ])

    def test_mode_history_omits_unchanged_initial_mode(self):
        dec = run([ev(0, ctrl(0x20)), ev(100, *text("A"))])
        self.assertEqual(dec.modes, [])

    def test_mode_history_records_transitions(self):
        dec = run([
            ev(0, ctrl(0x25)),
            ev(100, ctrl(0x29)),
            ev(200, ctrl(0x20)),
        ])
        self.assertEqual(dec.modes, [
            {"t": 0, "mode": "roll-up"},
            {"t": 100, "mode": "paint-on"},
            {"t": 200, "mode": "pop-on"},
        ])

    def test_chunking_does_not_change_control_actions(self):
        """Splitting the same byte stream differently must not add actions."""
        pairs = [ctrl(0x25), [ord("A"), ord("B")], ctrl(0x2D),
                 [ord("C"), ord("D")], ctrl(0x2D), [ord("E"), ord("F")]]
        whole = run([ev(0, *pairs)])
        split = run([ev(i * 10, p) for i, p in enumerate(pairs)])
        self.assertEqual(screens(whole)[-1], screens(split)[-1])
        self.assertEqual(whole.modes, split.modes)

    def test_random_chunking_is_equivalent(self):
        """Any chunk split of one pair stream dispatches the same actions.

        Snapshot count legitimately differs (one per event), so the
        invariant checked is the resulting state and the mode history: a
        re-read must never add or lose a control action.
        """
        import random
        rng = random.Random(6081)
        controls = [ctrl(0x25), ctrl(0x25, 0x1C), ctrl(0x2D), ctrl(0x21),
                    ctrl(0x2F), ctrl(0x20), ctrl(0x29), ctrl(0x2C),
                    ctrl(0x2E), [0x80, 0x80]]
        chars = [ord(c) for c in "ABCDEFGHIJ"]
        for _ in range(200):
            pairs = []
            for _ in range(rng.randint(1, 14)):
                if rng.random() < 0.45:
                    pairs.append(rng.choice(controls))
                else:
                    pairs.append([rng.choice(chars), rng.choice(chars)])
            whole = run([ev(0, *pairs)])
            max_cuts = len(pairs) - 1
            cuts = (sorted(rng.sample(range(1, len(pairs)),
                                      min(max_cuts, rng.randint(1, 4))))
                    if max_cuts > 0 else [])
            bounds = [0] + cuts + [len(pairs)]
            split = run([ev(0, *pairs[a:b])
                         for a, b in zip(bounds, bounds[1:])])
            self.assertEqual(whole.snapshots[-1]["screen"],
                             split.snapshots[-1]["screen"])
            # Timestamps are a test artifact (all chunks share t=0), so the
            # invariant is the sequence of mode changes, not their times.
            self.assertEqual([m["mode"] for m in whole.modes],
                             [m["mode"] for m in split.modes])


if __name__ == "__main__":
    unittest.main()