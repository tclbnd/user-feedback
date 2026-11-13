"""Acceptance tests for the streaming rational-ratio PCM converter."""
import math
import random
import unittest

import resample


def rand_frames(n, channels, seed=0):
    rng = random.Random(seed)
    return [[rng.uniform(-1.0, 1.0) for _ in range(channels)] for _ in range(n)]


def stream(in_rate, out_rate, channels, frames, chunks, discontinuity_after=()):
    conv = resample.Converter(in_rate, out_rate, channels)
    out = []
    pos = 0
    for index, size in enumerate(chunks):
        out.extend(
            conv.push(
                frames[pos:pos + size],
                discontinuity=index in discontinuity_after,
            )
        )
        pos += size
    out.extend(conv.finish())
    return out, conv.status()


def max_error(a, b):
    return max(
        (abs(x - y) for fa, fb in zip(a, b) for x, y in zip(fa, fb)),
        default=0.0,
    )


class BlockInvarianceTest(unittest.TestCase):
    def test_whole_ones_and_random_blocks_agree(self):
        in_rate, out_rate, channels, n = 44100, 48000, 2, 900
        frames = rand_frames(n, channels, 1)
        ref = resample.reference_resample(in_rate, out_rate, channels, frames)

        whole, whole_status = stream(in_rate, out_rate, channels, frames, [n])

        random_chunks = []
        rng = random.Random(9)
        remaining = n
        while remaining:
            size = min(remaining, rng.choice([0, 1, 1, 2, 5, 17, 64, 200]))
            random_chunks.append(size)
            remaining -= size
        shuffled, shuffled_status = stream(
            in_rate, out_rate, channels, frames, random_chunks
        )

        # Block-size one is the degenerate case of the random split.
        ones, ones_status = stream(in_rate, out_rate, channels, frames, [1] * n)

        expected = resample.output_length(in_rate, out_rate, n)
        for out in (whole, shuffled, ones):
            self.assertEqual(len(out), expected)
            self.assertLessEqual(max_error(out, ref), 2e-6)

        self.assertEqual(whole_status["next_output_index"],
                         shuffled_status["next_output_index"])
        self.assertEqual(whole_status["phase_numerator"],
                         shuffled_status["phase_numerator"])
        self.assertEqual(ones_status["next_output_index"],
                         whole_status["next_output_index"])
        self.assertEqual(ones_status["phase_numerator"],
                         whole_status["phase_numerator"])
        self.assertEqual(whole_status["buffered_frames"], 0)

    def test_empty_blocks_do_not_change_phase(self):
        conv_a = resample.Converter(44100, 48000, 1)
        conv_b = resample.Converter(44100, 48000, 1)
        frames = rand_frames(50, 1, 2)
        out_a = conv_a.push(frames) + conv_a.finish()
        out_b = []
        for frame in frames:
            out_b.extend(conv_b.push([]))
            out_b.extend(conv_b.push([frame]))
        out_b.extend(conv_b.finish())
        self.assertEqual(out_a, out_b)
        self.assertEqual(conv_a.status()["phase_numerator"],
                         conv_b.status()["phase_numerator"])

    def test_first_and_last_impulse_positions(self):
        n = 300
        frames = [[0.0]] * n
        frames[0] = [1.0]
        frames[-1] = [1.0]
        ref = resample.reference_resample(44100, 48000, 1, frames)
        # Direct convolution from the documented continuous kernel.
        p, q = resample._reduced_ratio(44100, 48000)
        fc = 0.45 * min(1.0, p / q)
        for m, value in enumerate(ref):
            t = m * q / p
            direct = sum(
                frames[i][0] * resample.kernel(i - t, fc) for i in range(n)
            )
            self.assertAlmostEqual(value[0], direct, places=12)
        first = next(i for i, v in enumerate(ref) if abs(v[0]) > 1e-12)
        last = max(i for i, v in enumerate(ref) if abs(v[0]) > 1e-12)
        self.assertEqual(first, 0)
        self.assertEqual(last, len(ref) - 1)


class DiscontinuityTest(unittest.TestCase):
    def test_segment_boundaries_isolate_signal(self):
        rng = random.Random(4)
        first = [[rng.uniform(-1, 1)] for _ in range(120)]
        second = [[0.0]] * 120
        conv = resample.Converter(48000, 44100, 1)
        out_a = conv.push(first)
        out_tail = conv.mark_discontinuity(flush=True)
        out_b = conv.push(second) + conv.finish()
        ref_a = resample.reference_resample(48000, 44100, 1, first)
        self.assertEqual(len(out_a) + len(out_tail), len(ref_a))
        self.assertLessEqual(max_error(out_a + out_tail, ref_a), 2e-6)
        # Nothing from the first segment may appear in the second.
        self.assertEqual(max(abs(f[0]) for f in out_b), 0.0)

    def test_push_with_flag_starts_new_segment(self):
        rng = random.Random(5)
        first = rand_frames(80, 2, 5)
        second = rand_frames(90, 2, 6)
        conv = resample.Converter(44100, 48000, 2)
        out = conv.push(first)
        out += conv.push(second, discontinuity=True)
        out += conv.finish()
        expected = len(resample.reference_resample(44100, 48000, 2, first))
        ref_a = resample.reference_resample(44100, 48000, 2, first)
        ref_b = resample.reference_resample(44100, 48000, 2, second)
        self.assertEqual(len(out), len(ref_a) + len(ref_b))
        self.assertLessEqual(max_error(out[:expected], ref_a), 2e-6)
        self.assertLessEqual(max_error(out[expected:], ref_b), 2e-6)


class ErrorHandlingTest(unittest.TestCase):
    def test_bad_push_leaves_state_usable(self):
        frames = rand_frames(60, 2, 7)
        conv = resample.Converter(44100, 48000, 2)
        out = conv.push(frames)
        before = conv.status()
        for bad in ([[1.0]], [[1.0, 2.0, 3.0]], [[float("nan"), 0.0]]):
            with self.assertRaises(ValueError):
                conv.push(bad)
        self.assertEqual(conv.status(), before)
        ref = resample.reference_resample(44100, 48000, 2, frames)
        self.assertLessEqual(max_error(out, ref[:len(out)]), 2e-6)

    def test_push_after_finish_requires_reset(self):
        conv = resample.Converter(44100, 48000, 1)
        conv.push([[1.0]] * 5)
        conv.finish()
        self.assertEqual(conv.finish(), [])
        with self.assertRaises(ValueError):
            conv.push([[1.0]])
        conv.reset()
        self.assertEqual(conv.status()["fractional_phase"], 0.0)
        out = conv.push([[1.0]] * 5) + conv.finish()
        ref = resample.reference_resample(44100, 48000, 1, [[1.0]] * 5)
        self.assertLessEqual(max_error(out, ref), 2e-6)


class ResponseTest(unittest.TestCase):
    def amplitude(self, out, freq, skip=400):
        re = im = 0.0
        count = 0
        for m in range(skip, len(out) - 120):
            phase = 2 * math.pi * freq * m
            re += out[m][0] * math.cos(phase)
            im += out[m][0] * math.sin(phase)
            count += 1
        return 2 * math.hypot(re, im) / count

    def test_downsampling_passband_and_alias_rejection(self):
        in_rate, out_rate, n = 48000, 44100, 3000
        p, q = resample._reduced_ratio(in_rate, out_rate)
        # Source-domain Nyquist of the 44.1 kHz target is 0.5*147/160.
        new_nyquist = 0.5 * p / q
        for source_freq in (0.05, 0.10, 0.20, 0.35, 0.39):
            frames = [[math.sin(2 * math.pi * source_freq * i)] for i in range(n)]
            out = resample.reference_resample(in_rate, out_rate, 1, frames)
            gain = self.amplitude(out, source_freq * q / p)
            self.assertLessEqual(abs(20 * math.log10(gain)), 0.2)
        # Only frequencies above the new Nyquist genuinely fold; frequencies
        # just below 1.0 are indistinguishable from the passband input and
        # therefore cannot be rejected by any filter.
        folded_band = (
            new_nyquist + 0.005,
            0.48,
            0.50,
            0.52,
            1.0 - new_nyquist - 0.005,
        )
        for source_freq in folded_band:
            frames = [[math.sin(2 * math.pi * source_freq * i)] for i in range(n)]
            out = resample.reference_resample(in_rate, out_rate, 1, frames)
            folded = abs(source_freq * q / p - round(source_freq * q / p))
            gain = self.amplitude(out, folded)
            self.assertLessEqual(20 * math.log10(gain), -65.0)


class ReplayErrorRecoveryTest(unittest.TestCase):
    def test_errors_then_continue(self):
        frames = [[float(i)] for i in range(30)]
        document = {
            "in_rate": 44100,
            "out_rate": 48000,
            "channels": 1,
            "commands": [
                {"op": "push", "frames": frames[:10]},
                {"op": "push", "frames": [[1.0, 2.0]]},
                {"op": "push", "frames": frames[10:20]},
                {"op": "finish"},
                {"op": "finish"},
                {"op": "push", "frames": [[5.0]]},
                {"op": "reset"},
                {"op": "push", "frames": frames[20:]},
                {"op": "finish"},
            ],
        }
        result = resample.replay(document)
        errors = [r.get("error") for r in result["results"] if r.get("error")]
        self.assertEqual(len(errors), 2)
        delivered = [f for r in result["results"] for f in r["frames"]]
        expected = (
            resample.reference_resample(44100, 48000, 1, frames[:20])
            + resample.reference_resample(44100, 48000, 1, frames[20:])
        )
        self.assertEqual(len(delivered), len(expected))
        self.assertLessEqual(max_error(delivered, expected), 2e-6)

    def test_discontinuous_push_after_finish(self):
        document = {
            "in_rate": 44100,
            "out_rate": 48000,
            "channels": 1,
            "commands": [
                {"op": "push", "frames": [[1.0]] * 3},
                {"op": "finish"},
                {"op": "push", "frames": [[2.0]] * 3, "discontinuity": True},
                {"op": "finish"},
            ],
        }
        streamed = resample.replay(document)
        control = resample.reference_replay(document)
        self.assertEqual([len(r["frames"]) for r in streamed["results"]],
                         [len(r["frames"]) for r in control["results"]])
        self.assertFalse(any(r.get("error") for r in streamed["results"]))
        self.assertFalse(any(r.get("error") for r in control["results"]))
        a = [f for r in streamed["results"] for f in r["frames"]]
        b = [f for r in control["results"] for f in r["frames"]]
        self.assertEqual(len(a), len(b))
        self.assertLessEqual(max_error(a, b), 2e-6)
        self.assertEqual(streamed["results"][-1]["input_count"],
                         control["results"][-1]["input_count"])
        # And the plain form still requires a reset.
        plain = resample.Converter(44100, 48000, 1)
        plain.push([[1.0]] * 3)
        plain.finish()
        with self.assertRaises(ValueError):
            plain.push([[1.0]])


class ReplayTest(unittest.TestCase):
    def test_demo_document(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        result = resample.replay(json.loads((root / "demo.json").read_text()))
        self.assertEqual(len(result["results"]), 3)
        self.assertEqual(len(result["results"][2]["frames"]), 5)
        self.assertEqual(result["results"][2]["buffered_frames"], 0)

    def build_document(self):
        rng = random.Random(2)
        in_rate, out_rate, channels = 48000, 44100, 3
        commands = []
        for _ in range(40):
            size = rng.choice([0, 1, 2, 7, 40])
            commands.append({
                "op": "push",
                "frames": [
                    [rng.uniform(-1, 1) for _ in range(channels)]
                    for _ in range(size)
                ],
            })
        commands.append({"op": "finish"})
        commands.append({"op": "finish"})
        commands.append({"op": "reset"})
        for _ in range(20):
            commands.append({
                "op": "push",
                "frames": [[rng.uniform(-1, 1) for _ in range(channels)] for _ in range(5)],
            })
        commands.append({
            "op": "discontinuity",
            "frames": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
        })
        commands.append({"op": "finish"})
        commands.append({"op": "push", "frames": [[1.0, 0.0, 0.0]]})
        commands.append({"op": "push", "frames": [[1.0]]})
        commands.append({"op": "push", "frames": []})
        commands.append({"op": "stat"})
        commands.append({"op": "finish"})
        return {"in_rate": in_rate, "out_rate": out_rate,
                "channels": channels, "commands": commands}

    def test_reference_replay_matches_streaming_replay(self):
        document = self.build_document()
        streamed = resample.replay(document)
        control = resample.reference_replay(document)
        streamed_frames = [f for r in streamed["results"] for f in r["frames"]]
        control_frames = [f for r in control["results"] for f in r["frames"]]
        self.assertEqual(len(streamed_frames), len(control_frames))
        self.assertLessEqual(max_error(streamed_frames, control_frames), 2e-6)
        final_a = streamed["results"][-1]
        final_b = control["results"][-1]
        for key in ("input_count", "output_count", "segment_index", "finished"):
            self.assertEqual(final_a[key], final_b[key], key)
        # Input accounting is immediate in both paths, so it must match on
        # every command; a dropped or repeated block is caught immediately.
        for a, b in zip(streamed["results"], control["results"]):
            self.assertEqual(a["input_count"], b["input_count"])
        # The reference can only emit when a segment closes, so output counts
        # are compared at the end rather than per command.
        self.assertEqual(streamed["results"][-1]["output_count"],
                         control["results"][-1]["output_count"])
        # Errors are reported per command and do not poison later commands.
        errors_a = [r.get("error") for r in streamed["results"] if r.get("error")]
        errors_b = [r.get("error") for r in control["results"] if r.get("error")]
        self.assertEqual(errors_a, errors_b)
        self.assertTrue(errors_a)


if __name__ == "__main__":
    unittest.main()
