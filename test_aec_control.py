#!/usr/bin/env python3
"""Unit tests for the frame-energy AEC control contract (aec.process)."""
import unittest

from aec import process


def frame(t, far, near, mic):
    return {"t": t, "farend": far, "nearend": near, "mic": mic}


class TestAECControl(unittest.TestCase):

    def test_adapting_when_quiet_mic(self):
        doc = {"win": 10, "frames": [
            frame(0, 10.0, 0.0, 1.0),
            frame(1, 10.0, 0.0, 1.0),
        ]}
        out = process(doc)
        self.assertEqual([f["state"] for f in out["frames"]],
                         ["adapting", "adapting"])
        self.assertEqual(out["frames"][1]["used_est"], 7.0)
        self.assertEqual(out["alerts"], [])

    def test_double_talk_freezes_and_holds_estimate(self):
        doc = {"win": 10, "frames": [
            frame(0, 10.0, 0.0, 1.0),    # adapting, used=7.0
            frame(1, 10.0, 8.0, 9.0),    # ser high -> freeze, hold 7.0
            frame(2, 10.0, 8.0, 9.0),    # still frozen, held value
        ]}
        out = process(doc)
        states = [f["state"] for f in out["frames"]]
        self.assertEqual(states, ["adapting", "frozen", "frozen"])
        self.assertEqual([f["used_est"] for f in out["frames"]],
                         [7.0, 7.0, 7.0])  # never adopts est while frozen

    def test_hysteresis_resumes_on_next_frame(self):
        # est_prev ~ 7; hysteresis threshold 0.3*7 = 2.1
        doc = {"win": 10, "frames": [
            frame(0, 10.0, 0.0, 1.0),   # adapting used=7
            frame(1, 10.0, 8.0, 9.0),   # freeze
            frame(2, 10.0, 1.0, 9.0),   # near-end dropped: still frozen...
            frame(3, 10.0, 1.0, 1.0),   # ...resume on THIS frame
        ]}
        out = process(doc)
        self.assertEqual([f["state"] for f in out["frames"]],
                         ["adapting", "frozen", "frozen", "adapting"])

    def test_reference_silence_freezes(self):
        # win=1 and spaced timestamps so each frame only sees its own farend
        doc = {"win": 1, "frames": [
            frame(0, 10.0, 0.0, 1.0),
            frame(2, 0.0, 0.0, 0.0),    # reference silence
            frame(4, 0.0, 0.0, 0.0),
            frame(6, 10.0, 0.0, 1.0),   # active again -> adapting
        ]}
        out = process(doc)
        self.assertEqual([f["state"] for f in out["frames"]],
                         ["adapting", "frozen", "frozen", "adapting"])
        # held estimate through the gap
        self.assertEqual(out["frames"][2]["used_est"], 7.0)

    def test_delay_shift_alert_and_resync(self):
        doc = {"win": 1, "frames": [
            frame(0, 10.0, 0.0, 1.0),   # used=7
            frame(1, 30.0, 0.0, 1.0),   # est=21 -> 200% jump -> alert
        ]}
        out = process(doc)
        self.assertEqual(out["alerts"], [{"t": 1, "kind": "delay-shift"}])
        self.assertEqual(out["frames"][1]["state"], "adapting")
        self.assertEqual(out["frames"][1]["used_est"], 21.0)

    def test_small_jump_no_alert(self):
        doc = {"win": 1, "frames": [
            frame(0, 10.0, 0.0, 1.0),   # used=7
            frame(1, 12.0, 0.0, 1.0),   # est=8.4, 20% jump -> no alert
        ]}
        out = process(doc)
        self.assertEqual(out["alerts"], [])

    def test_rounding(self):
        doc = {"win": 0, "frames": [frame(0, 1.23456, 0.0, 0.2)]}
        out = process(doc)
        f = out["frames"][0]
        self.assertEqual(f["used_est"], round(0.7 * 1.23456, 2))
        self.assertEqual(f["ser"],
                         round(0.2 / (0.7 * 1.23456), 3))

    def test_cold_start_does_not_alert(self):
        doc = {"win": 1, "frames": [frame(0, 100.0, 0.0, 1.0)]}
        out = process(doc)
        self.assertEqual(out["alerts"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
