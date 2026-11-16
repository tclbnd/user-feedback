"""Acceptance tests for the partial resynthesis renderer.

The reference implementation below is a literal transcription of README.md;
the tests also check that phase is a pure function of absolute sample index,
so chunked/checkpointed rendering is sample-for-sample identical to a single
continuous render.
"""
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pitch  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def reference(d):
    """Direct transcription of README.md."""
    R, SR, N = d["ratio"], d["sr"], d["frames"]
    F = N // 4
    y = []
    for i in range(N):
        t = i / SR
        s = sum(p["amp"] * math.cos(2 * math.pi * (R * p["freq"]) * t + p["phase"])
                for p in d["partials"])
        if i < F:
            s *= 0.5 - 0.5 * math.cos(math.pi * (i + 1) / F)
        elif i >= N - F:
            s *= 0.5 - 0.5 * math.cos(math.pi * (N - i) / F)
        y.append(s)
    mean = sum(y) / N
    y = [v - mean for v in y]
    return {
        "samples": [round(v, 6) for v in y],
        "peak": round(max(abs(v) for v in y), 6),
        "freqs": [round(R * p["freq"], 3) for p in d["partials"]],
    }


CASES = {
    "single": {"partials": [{"freq": 440.0, "amp": 1.0, "phase": 0.0}],
               "ratio": 2.0, "sr": 8000, "frames": 64},
    "multi": {"partials": [{"freq": 220.0, "amp": 0.8, "phase": 0.1},
                           {"freq": 330.0, "amp": 0.4, "phase": -1.2},
                           {"freq": 550.5, "amp": 0.25, "phase": 3.0}],
              "ratio": 0.75, "sr": 44100, "frames": 257},
    "tiny": {"partials": [{"freq": 100.0, "amp": 0.5, "phase": 2.0}],
             "ratio": 1.5, "sr": 48000, "frames": 7},
    "dc": {"partials": [{"freq": 10.0, "amp": 1.0, "phase": 0.0}],
           "ratio": 1.0, "sr": 1000, "frames": 128},
}


class ReferenceTests(unittest.TestCase):
    def test_render_matches_readme(self):
        for name, d in CASES.items():
            with self.subTest(name=name):
                y = pitch.render(d["partials"], d["ratio"], d["sr"], d["frames"])
                got = {
                    "samples": [round(v, 6) for v in y],
                    "peak": round(max(abs(v) for v in y), 6),
                    "freqs": [round(d["ratio"] * p["freq"], 3)
                              for p in d["partials"]],
                }
                self.assertEqual(got, reference(d))

    def test_cli_matches_readme(self):
        for name, d in CASES.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                inp = os.path.join(td, "in.json")
                out = os.path.join(td, "out.json")
                with open(inp, "w") as fh:
                    json.dump(d, fh)
                env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
                subprocess.run([sys.executable, "-B",
                                os.path.join(ROOT, "pitch.py"), inp, out],
                               check=True, env=env)
                with open(out) as fh:
                    self.assertEqual(json.load(fh), reference(d))

    def test_fade_endpoints_and_shape(self):
        n, f = 16, 4
        self.assertAlmostEqual(pitch.fade_gain(0, n, f),
                               0.5 - 0.5 * math.cos(math.pi / f))
        self.assertAlmostEqual(pitch.fade_gain(f - 1, n, f), 1.0)
        self.assertEqual(pitch.fade_gain(f, n, f), 1.0)
        self.assertAlmostEqual(pitch.fade_gain(n - f, n, f), 1.0)
        self.assertAlmostEqual(pitch.fade_gain(n - 1, n, f),
                               0.5 - 0.5 * math.cos(math.pi / f))
        # monotone edge ramps, flat interior
        left = [pitch.fade_gain(i, n, f) for i in range(f)]
        right = [pitch.fade_gain(i, n, f) for i in range(n - f, n)]
        self.assertEqual(left, sorted(left))
        self.assertEqual(right, sorted(right, reverse=True))

    def test_no_divide_by_zero_on_tiny_frame_count(self):
        y = pitch.render([{"freq": 5.0, "amp": 1.0, "phase": 0.0}],
                         1.0, 100, 3)
        self.assertEqual(len(y), 3)
        self.assertTrue(all(math.isfinite(v) for v in y))

    def test_dc_removed_once_after_fade(self):
        d = CASES["dc"]
        y = pitch.render(d["partials"], d["ratio"], d["sr"], d["frames"])
        self.assertAlmostEqual(sum(y) / len(y), 0.0, places=12)


class ContinuityTests(unittest.TestCase):
    def test_phase_is_absolute(self):
        p = {"freq": 440.0, "amp": 1.0, "phase": 0.3}
        for i in (0, 1, 17, 1000):
            t = i / 48000
            self.assertAlmostEqual(
                pitch.partial_sample(p, 1.5, 48000, i),
                math.cos(2 * math.pi * 1.5 * 440.0 * t + 0.3),
                places=15,
            )

    def test_chunked_equals_continuous(self):
        d = CASES["multi"]
        whole = pitch.render(d["partials"], d["ratio"], d["sr"], d["frames"])
        for splits in ([1, 2, 63, 64, 200], [128], [256], [0]):
            state = pitch.checkpoint_state(d["partials"], d["ratio"], d["sr"],
                                           d["frames"], 0, [])
            for stop in splits + [d["frames"]]:
                _, state = pitch.resume(state, stop)
            self.assertEqual(pitch.finish(state), whole, msg=str(splits))

    def test_checkpoint_roundtrip_via_json(self):
        d = CASES["multi"]
        whole = pitch.render(d["partials"], d["ratio"], d["sr"], d["frames"])
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "ckpt.json")
            state = pitch.checkpoint_state(d["partials"], d["ratio"], d["sr"],
                                           d["frames"], 0, [])
            _, state = pitch.resume(state, 100)
            pitch.save_checkpoint(path, state)
            restored = pitch.load_checkpoint(path)
        _, restored = pitch.resume(restored)
        self.assertEqual(pitch.finish(restored), whole)
        self.assertEqual(restored["next_index"], d["frames"])

    def test_resume_at_arbitrary_offset_matches_tail(self):
        d = CASES["dc"]
        whole = pitch.render(d["partials"], d["ratio"], d["sr"], d["frames"])
        faded = pitch.render_range(d["partials"], d["ratio"], d["sr"],
                                   d["frames"], 0, d["frames"])
        state = pitch.checkpoint_state(d["partials"], d["ratio"], d["sr"],
                                       d["frames"], 0, [])
        _, state = pitch.resume(state, 37)
        # samples before the checkpoint were already emitted by a prior run
        self.assertEqual(state["faded"], faded[:37])
        chunk, state = pitch.resume(state)
        self.assertEqual(chunk, faded[37:])
        self.assertEqual(pitch.finish(state), whole)


class ControlTests(unittest.TestCase):
    """Acceptance fixtures for absolute-time control handling."""

    SR = 1000
    PARTIALS = [{"freq": 50.0, "amp": 1.0, "phase": 0.0}]

    def _theta(self, frames, controls, interp="linear"):
        curves = pitch.build_curves(self.PARTIALS, 1.0, self.SR, frames,
                                    controls, interp)
        return curves[0]["theta"], curves[0]["amp"]

    def test_piecewise_sweep_frequency_curve(self):
        frames = 200
        controls = [{"at": 0, "ratio": 1.0},
                    {"at": 100, "ratio": 2.0},
                    {"at": 199, "ratio": 0.5}]
        theta, _ = self._theta(frames, controls)
        # phase advance per sample follows the interpolated ratio exactly
        for i in range(frames - 1):
            expected = 2 * math.pi * 50.0 / self.SR * (
                1.0 + (i / 100.0) if i < 100 else
                (2.0 - 1.5 * (i - 100) / 99.0 if i < 199 else 0.5))
            self.assertAlmostEqual(theta[i + 1] - theta[i], expected, places=9)
        # the phase curve itself is continuous across the control points:
        # no sample-to-sample jump is larger than one full step at the
        # highest ratio in the sweep
        max_step = 2 * math.pi * 50.0 * 2.0 / self.SR * 1.001
        for at in (99, 100, 198):
            self.assertLess(abs(theta[at + 1] - theta[at]), max_step)

    def test_hold_interpolation(self):
        frames = 40
        controls = [{"at": 0, "amp": 1.0}, {"at": 20, "amp": 0.0}]
        _, amp_linear = self._theta(frames, controls, "linear")
        _, amp_hold = self._theta(frames, controls, "hold")
        self.assertAlmostEqual(amp_linear[10], 0.5)
        self.assertEqual(amp_hold[10], 1.0)
        self.assertEqual(amp_hold[19], 1.0)
        self.assertEqual(amp_hold[20], 0.0)

    def test_mute_then_restore_keeps_phase_running(self):
        frames = 120
        # hold mode makes the mute a true silent span rather than a ramp
        controls = [{"at": 40, "amp": 0.0}, {"at": 80, "amp": 1.0}]
        theta, amp = self._theta(frames, controls, "hold")
        step = 2 * math.pi * 50.0 / self.SR
        # silence must not stop the oscillator: phase still advances by `step`
        for i in (40, 60, 79):
            self.assertAlmostEqual(theta[i + 1] - theta[i], step, places=12)
            self.assertAlmostEqual(amp[i], 0.0, places=12)
        # restored amplitude resumes at the phase continuous playback reached
        self.assertAlmostEqual(theta[80], step * 80, places=9)
        self.assertAlmostEqual(amp[80], 1.0, places=12)

    def test_linear_amplitude_ramp(self):
        frames = 120
        controls = [{"at": 40, "amp": 0.0}, {"at": 80, "amp": 1.0}]
        _, amp = self._theta(frames, controls, "linear")
        # sample 0 anchors the initial gain, so the ramp is continuous into
        # the first control point and between control points
        self.assertAlmostEqual(amp[0], 1.0, places=12)
        self.assertAlmostEqual(amp[20], 0.5, places=12)
        self.assertAlmostEqual(amp[39], 0.025, places=12)
        self.assertAlmostEqual(amp[40], 0.0, places=12)
        self.assertAlmostEqual(amp[60], 0.5, places=12)
        self.assertAlmostEqual(amp[79], 0.975, places=12)
        self.assertAlmostEqual(amp[80], 1.0, places=12)

    def test_frequency_only_controls_leave_gain_constant(self):
        frames = 100
        controls = [{"at": 0, "ratio": 1.0}, {"at": 50, "ratio": 1.5}]
        _, amp = self._theta(frames, controls)
        self.assertTrue(all(v == 1.0 for v in amp))

    def test_explicit_reset_reanchors_phase(self):
        frames = 80
        controls = [{"at": 40, "reset": True, "phase": -0.5}]
        theta, _ = self._theta(frames, controls)
        step = 2 * math.pi * 50.0 / self.SR
        self.assertAlmostEqual(theta[40], -0.5, places=12)
        self.assertAlmostEqual(theta[41], -0.5 + step, places=12)
        self.assertAlmostEqual(theta[39], step * 39, places=9)

    def test_no_reset_control_leaves_phase_untouched(self):
        frames = 80
        controls = [{"at": 40, "ratio": 1.0, "amp": 0.5}]
        theta, amp = self._theta(frames, controls)
        step = 2 * math.pi * 50.0 / self.SR
        for i in range(frames - 1):
            self.assertAlmostEqual(theta[i + 1] - theta[i], step, places=12)
        self.assertAlmostEqual(theta[40], step * 40, places=9)
        self.assertAlmostEqual(amp[40], 0.5)

    def test_same_sample_order_is_deterministic(self):
        frames = 60
        a = [{"at": 30, "amp": 0.25}, {"at": 30, "ratio": 1.5}]
        b = [{"at": 30, "ratio": 1.5}, {"at": 30, "amp": 0.25}]
        ta, aa = self._theta(frames, a)
        tb, ab = self._theta(frames, b)
        self.assertEqual(ta, tb)
        self.assertEqual(aa, ab)
        # a later reset at the same sample overrides an earlier one
        c = [{"at": 30, "reset": True, "phase": 0.0},
             {"at": 30, "reset": True, "phase": 1.0}]
        theta, _ = self._theta(frames, c)
        self.assertAlmostEqual(theta[30], 1.0)

    def test_event_jitter_snaps_to_nearest_sample(self):
        frames = 50
        lo = pitch.normalize_controls([{"at": 10.2}], frames)
        hi = pitch.normalize_controls([{"at": 10.6}], frames)
        self.assertEqual(lo[0][0], 10)
        self.assertEqual(hi[0][0], 11)
        # negative and past-the-end instants clamp into the render window
        clamped = pitch.normalize_controls([{"at": -3}, {"at": 999}], frames)
        self.assertEqual([c[0] for c in clamped], [0, frames])

    def test_controls_are_order_independent_in_input(self):
        frames = 100
        base = [{"at": 25, "ratio": 1.25}, {"at": 60, "amp": 0.4},
                {"at": 75, "reset": True, "phase": 0.0}]
        shuffled = [base[2], base[0], base[1]]
        r1 = pitch.render(self.PARTIALS, 1.0, self.SR, frames, base)
        r2 = pitch.render(self.PARTIALS, 1.0, self.SR, frames, shuffled)
        self.assertEqual(r1, r2)


class ControlContinuityTests(unittest.TestCase):
    def setUp(self):
        self.d = {
            "partials": [{"freq": 220.0, "amp": 0.8, "phase": 0.1},
                         {"freq": 331.0, "amp": 0.3, "phase": -2.0}],
            "ratio": 1.0, "sr": 8000, "frames": 300,
            "controls": [{"at": 0, "ratio": 1.0},
                         {"at": 50, "ratio": 1.5},
                         {"at": 120, "amp": 0.0},
                         {"at": 180, "amp": 0.7},
                         {"at": 240, "reset": True, "phase": 0.25},
                         {"at": 299, "ratio": 0.8}],
        }

    def test_chunked_controls_equal_continuous(self):
        d = self.d
        whole = pitch.render(d["partials"], d["ratio"], d["sr"], d["frames"],
                            d["controls"], d["interp"] if "interp" in d else "linear")
        for splits in ([0, 1, 49, 50, 51, 119, 120, 179, 240, 298],
                       [137], [299], [0]):
            state = pitch.checkpoint_state(d["partials"], d["ratio"], d["sr"],
                                           d["frames"], 0, [],
                                           d["controls"])
            for stop in splits + [d["frames"]]:
                _, state = pitch.resume(state, stop)
            self.assertEqual(pitch.finish(state), whole, msg=str(splits))

    def test_checkpoint_with_controls_roundtrips_through_json(self):
        d = self.d
        whole = pitch.render(d["partials"], d["ratio"], d["sr"], d["frames"],
                            d["controls"])
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "ckpt.json")
            state = pitch.checkpoint_state(d["partials"], d["ratio"], d["sr"],
                                           d["frames"], 0, [],
                                           d["controls"])
            _, state = pitch.resume(state, 121)
            pitch.save_checkpoint(path, state)
            state = pitch.load_checkpoint(path)
        _, state = pitch.resume(state)
        self.assertEqual(pitch.finish(state), whole)

    def test_cli_accepts_controls(self):
        d = self.d
        with tempfile.TemporaryDirectory() as td:
            inp = os.path.join(td, "in.json")
            out = os.path.join(td, "out.json")
            with open(inp, "w") as fh:
                json.dump(d, fh)
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            subprocess.run([sys.executable, "-B",
                            os.path.join(ROOT, "pitch.py"), inp, out],
                           check=True, env=env)
            with open(out) as fh:
                got = json.load(fh)
        y = pitch.render(d["partials"], d["ratio"], d["sr"], d["frames"],
                         d["controls"])
        self.assertEqual(got["samples"], [round(v, 6) for v in y])

    def test_cli_blocked_render_matches_continuous(self):
        d = self.d
        with tempfile.TemporaryDirectory() as td:
            inp = os.path.join(td, "in.json")
            with open(inp, "w") as fh:
                json.dump(d, fh)
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            outputs = []
            for name, extra in (("whole", []),
                                ("blocked", ["--block", "7"]),
                                ("blocked1", ["--block", "1"])):
                out = os.path.join(td, name + ".json")
                ckpt = os.path.join(td, name + ".ckpt.json")
                subprocess.run([sys.executable, "-B",
                                os.path.join(ROOT, "pitch.py"), inp, out,
                                "--checkpoint", ckpt] + extra,
                               check=True, env=env)
                with open(out) as fh:
                    outputs.append(json.load(fh))
                state = pitch.load_checkpoint(ckpt)
                self.assertEqual(state["next_index"], d["frames"])
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(outputs[0], outputs[2])

    def test_saved_checkpoint_resumes_to_identical_output(self):
        d = self.d
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "ckpt.json")
            state = pitch.checkpoint_state(d["partials"], d["ratio"], d["sr"],
                                           d["frames"], 0, [],
                                           d["controls"])
            _, state = pitch.resume(state, 137)
            pitch.save_checkpoint(path, state)
            resumed = pitch.load_checkpoint(path)
            self.assertEqual(resumed["next_index"], 137)
            _, resumed = pitch.resume(resumed)
            got = pitch.finish(resumed)
        whole = pitch.render(d["partials"], d["ratio"], d["sr"], d["frames"],
                            d["controls"])
        self.assertEqual(got, whole)

    def test_checkpoint_signature_rejects_mismatch(self):
        d = self.d
        state = pitch.checkpoint_state(d["partials"], d["ratio"], d["sr"],
                                       d["frames"], 0, [], d["controls"])
        state["ratio"] = 2.0
        with self.assertRaises(ValueError):
            pitch.resume(state)
        d = CASES["multi"]
        with tempfile.TemporaryDirectory() as td:
            inp = os.path.join(td, "in.json")
            out = os.path.join(td, "out.json")
            with open(inp, "w") as fh:
                json.dump(d, fh)
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            subprocess.run([sys.executable, "-B",
                            os.path.join(ROOT, "pitch.py"), inp, out],
                           check=True, env=env)
            with open(out) as fh:
                self.assertEqual(json.load(fh), reference(d))


class FinishGuardTests(unittest.TestCase):
    def test_finish_rejects_incomplete_state(self):
        state = pitch.checkpoint_state([{"freq": 1, "amp": 1, "phase": 0}],
                                       1.0, 10, 4, 2, [])
        with self.assertRaises(ValueError):
            pitch.finish(state)


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(name):
    with open(os.path.join(FIXTURES, name + ".json")) as fh:
        return json.load(fh)


class FixtureTests(unittest.TestCase):
    """Run the four control fixtures through the real CLI."""

    def _run(self, name, extra=()):
        d = load_fixture(name)
        with tempfile.TemporaryDirectory() as td:
            inp = os.path.join(FIXTURES, name + ".json")
            out = os.path.join(td, "out.json")
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            subprocess.run([sys.executable, "-B",
                            os.path.join(ROOT, "pitch.py"), inp, out] + list(extra),
                           check=True, env=env)
            with open(out) as fh:
                got = json.load(fh)
        return d, got

    def test_sweep_frequency_curve(self):
        d, got = self._run("sweep")
        expected = [round(d["ratio"] * p["freq"], 3) for p in d["partials"]]
        self.assertEqual(got["freqs"], expected)
        curves = pitch.build_curves(d["partials"], d["ratio"], d["sr"],
                                    d["frames"], d["controls"],
                                    d.get("interp", "linear"))
        theta = curves[0]["theta"]
        step = 2 * math.pi * d["partials"][0]["freq"] / d["sr"]
        # instantaneous frequency follows the interpolated ratio
        for at, ratio in ((0, 1.0), (50, 1.5), (99, 1.99), (100, 2.0),
                          (175, 1.75), (250, 1.5),
                          (300, 1.5 - 50.0 / 149.0),
                          (398, 1.5 - 148.0 / 149.0)):
            got_ratio = (theta[at + 1] - theta[at]) / step
            self.assertAlmostEqual(got_ratio, ratio, delta=2e-3)
        # no jumps at the control points themselves
        for at in (99, 100, 249, 250):
            self.assertLess(abs(theta[at + 1] - theta[at]),
                            step * 2.0 * 1.001)

    def test_mute_restore_holds_phase(self):
        d, got = self._run("mute_restore")
        self.assertEqual(len(got["samples"]), d["frames"])
        curves = pitch.build_curves(d["partials"], d["ratio"], d["sr"],
                                    d["frames"], d["controls"],
                                    d.get("interp", "linear"))
        theta = curves[0]["theta"]
        amp = curves[0]["amp"]
        # ratio has no control points, so it stays at the note's ratio
        step = (2 * math.pi * d["partials"][0]["freq"] * d["ratio"] / d["sr"])
        # the muted span is exactly zero and the phase keeps advancing
        for i in range(40, 200):
            self.assertEqual(amp[i], 0.0)
            self.assertAlmostEqual(theta[i + 1] - theta[i], step, places=12)
        self.assertEqual(amp[200], d["partials"][0]["amp"])
        # after restoration the phase is what continuous playback reached,
        # offset by the partial's initial phase
        self.assertAlmostEqual(theta[200],
                               d["partials"][0]["phase"] + step * 200,
                               places=9)

    def test_explicit_reset_reanchors(self):
        d, got = self._run("reset")
        self.assertEqual(len(got["samples"]), d["frames"])
        theta = pitch.build_curves(d["partials"], d["ratio"], d["sr"],
                                   d["frames"], d["controls"])[0]["theta"]
        self.assertAlmostEqual(theta[60], 0.0, places=12)
        self.assertAlmostEqual(theta[180], 1.0, places=12)
        step_a = 2 * math.pi * 150.0 / d["sr"]
        step_b = 2 * math.pi * 150.0 * 1.5 / d["sr"]
        self.assertAlmostEqual(theta[61] - theta[60], step_a, places=12)
        self.assertAlmostEqual(theta[181] - theta[180], step_b, places=12)
        # the ratio ramps from 1.0 to 1.5 between samples 60 and 120, so the
        # phase advances faster than step_a just before that control point
        self.assertGreater(theta[120] - theta[119], step_a)

    def test_no_reset_controls_keep_phase_continuous(self):
        d, got = self._run("no_reset")
        self.assertEqual(len(got["samples"]), d["frames"])
        theta = pitch.build_curves(d["partials"], d["ratio"], d["sr"],
                                   d["frames"], d["controls"])[0]["theta"]
        for at in (60, 120, 180):
            before = (theta[at] - theta[at - 1])
            after = (theta[at + 1] - theta[at])
            self.assertLess(abs(after - before),
                            0.5 * 2 * math.pi * 150.0 * 2.0 / d["sr"])
        self.assertAlmostEqual(theta[0], 0.0, places=12)

    def test_fixtures_chunked_equals_continuous(self):
        for name in ("sweep", "mute_restore", "reset", "no_reset"):
            d = load_fixture(name)
            controls = d["controls"]
            interp = d.get("interp", "linear")
            whole = pitch.render(d["partials"], d["ratio"], d["sr"],
                                 d["frames"], controls, interp)
            for block in (1, 7, 64, d["frames"] // 3 or 1):
                got = pitch.render_blocks(d["partials"], d["ratio"], d["sr"],
                                          d["frames"], controls, interp, block)
                self.assertEqual(got, whole, msg="%s block=%d" % (name, block))


if __name__ == "__main__":
    unittest.main()
