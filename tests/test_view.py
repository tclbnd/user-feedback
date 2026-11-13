"""Acceptance tests for the APNG viewer (standard library only).

Run with ``python3 -m unittest discover -s tests -t .`` from the project root.
"""
import hashlib
import json
import random
import struct
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import fixtures  # noqa: E402
import view  # noqa: E402

WIDTH, HEIGHT, COUNT, BUDGET = 7, 5, 120, 4


def _fixture_frames():
    return fixtures.tricky_fixture(WIDTH, HEIGHT, COUNT)


def _fixture_png(default_image=False):
    return fixtures.build(WIDTH, HEIGHT, _fixture_frames(),
                          default_image=default_image)


class SequentialReferenceTest(unittest.TestCase):
    """Every one of the 120 frames must match the independent baseline."""

    def setUp(self):
        self.frames = _fixture_frames()
        self.data = fixtures.build(WIDTH, HEIGHT, self.frames)
        self.reference = fixtures.reference_frames(WIDTH, HEIGHT, self.frames)
        self.starts = fixtures.starts(self.frames)

    def test_fixture_really_chains_previous_and_background(self):
        # The same pixel must be covered by runs of consecutive PREVIOUS and
        # consecutive BACKGROUND frames; this guards the fixture itself.
        pixel = (2, 2)
        for index in range(COUNT):
            frame = self.frames[index]
            inside = (frame["x"] <= pixel[0] < frame["x"] + frame["w"]
                      and frame["y"] <= pixel[1] < frame["y"] + frame["h"])
            self.assertTrue(inside, f"frame {index} misses {pixel}")
        dispose_chain = [f["dispose"] for f in self.frames[1:6]]
        self.assertEqual(dispose_chain, [2, 0, 1, 1, 2])
        for mode, minimum in ((2, 2), (1, 2)):
            longest = run = 0
            for frame in self.frames:
                run = run + 1 if frame["dispose"] == mode else 0
                longest = max(longest, run)
            self.assertGreaterEqual(longest, minimum,
                                    f"no run of {minimum} dispose_op={mode}")
        blends = {f["blend"] for f in self.frames}
        self.assertEqual(blends, {0, 1}, "fixture must use both blend modes")
        alphas = {f["pixels"](x, y)[3] for f in self.frames
                  for x in range(f["w"]) for y in range(f["h"])}
        self.assertTrue({0, 255} <= alphas, "fixture needs opaque and clear")
        self.assertTrue(any(0 < a < 255 for a in alphas),
                        "fixture needs partial alpha for OVER rounding")

    def test_every_frame_matches_baseline(self):
        result = view.view(self.data, None, BUDGET)
        self.assertEqual(len(result["frames"]), COUNT)
        for index, entry in enumerate(result["frames"]):
            expected = hashlib.sha256(
                fixtures.rgba_bytes(self.reference[index])).hexdigest()
            self.assertEqual(entry["rgba_sha256"], expected, f"frame {index}")
            self.assertEqual(entry["index"], index)
            start = self.starts[index]
            self.assertEqual(entry["start"], [start.numerator, start.denominator])
            duration = Fraction(self.frames[index]["delay_num"],
                                self.frames[index]["delay_den"] or 100)
            self.assertEqual(entry["duration"],
                             [duration.numerator, duration.denominator])

    def test_animation_frames_after_default_image_match_baseline(self):
        data = fixtures.build(WIDTH, HEIGHT, self.frames, default_image=True)
        result = view.view(data, None, BUDGET)
        for index, entry in enumerate(result["frames"]):
            expected = hashlib.sha256(
                fixtures.rgba_bytes(self.reference[index])).hexdigest()
            self.assertEqual(entry["rgba_sha256"], expected, f"frame {index}")

    def test_transparent_outside_local_update(self):
        # A frame that only covers a corner must leave the rest transparent:
        # nothing from an earlier frame may bleed into new visible area.
        rows = fixtures.reference_frames(WIDTH, HEIGHT, [
            {"x": 3, "y": 4, "w": 1, "h": 1,
             "pixels": lambda x, y: bytes((1, 2, 3, 4)),
             "delay_num": 1, "delay_den": 25, "dispose": 0, "blend": 0}])[0]
        for y in range(HEIGHT):
            for x in range(WIDTH):
                if (x, y) == (3, 4):
                    self.assertEqual(rows[y][x], (1, 2, 3, 4))
                else:
                    self.assertEqual(rows[y][x], (0, 0, 0, 0))

    def test_background_disposal_clears_only_the_rectangle(self):
        frames = [
            {"x": 0, "y": 0, "w": WIDTH, "h": HEIGHT,
             "pixels": lambda x, y: bytes((10, 20, 30, 255)),
             "delay_num": 1, "delay_den": 25, "dispose": 0, "blend": 0},
            {"x": 1, "y": 1, "w": 3, "h": 2,
             "pixels": lambda x, y: bytes((200, 100, 50, 255)),
             "delay_num": 1, "delay_den": 25, "dispose": 1, "blend": 0},
            {"x": 4, "y": 3, "w": 1, "h": 1,
             "pixels": lambda x, y: bytes((0, 0, 255, 255)),
             "delay_num": 1, "delay_den": 25, "dispose": 0, "blend": 0},
        ]
        displayed = fixtures.reference_frames(WIDTH, HEIGHT, frames)[2]
        for y in range(HEIGHT):
            for x in range(WIDTH):
                if (x, y) == (4, 3):
                    self.assertEqual(displayed[y][x], (0, 0, 255, 255))
                elif 1 <= x < 4 and 1 <= y < 3:
                    self.assertEqual(displayed[y][x], (0, 0, 0, 0))
                else:
                    self.assertEqual(displayed[y][x], (10, 20, 30, 255))


class RandomAccessTest(unittest.TestCase):
    """Shuffled jumps must keep both the RGBA canvas and the start times."""

    def setUp(self):
        self.frames = _fixture_frames()
        self.data = fixtures.build(WIDTH, HEIGHT, self.frames)
        self.reference = [hashlib.sha256(fixtures.rgba_bytes(rows)).hexdigest()
                          for rows in fixtures.reference_frames(
                              WIDTH, HEIGHT, self.frames)]
        self.starts = fixtures.starts(self.frames)

    def _check(self, requests, result):
        self.assertEqual(len(result["frames"]), len(requests))
        for position, entry in enumerate(result["frames"]):
            index = requests[position]
            self.assertEqual(entry["index"], index)
            self.assertEqual(entry["rgba_sha256"], self.reference[index],
                             f"request {position} frame {index}")
            start = self.starts[index]
            self.assertEqual(entry["start"], [start.numerator, start.denominator])

    def test_400_seeded_random_jumps(self):
        rng = random.Random(2024)
        requests = [rng.randrange(COUNT) for _ in range(400)]
        self._check(requests, view.view(self.data, requests, BUDGET))

    def test_forward_and_reverse_playback_agree_with_sequential(self):
        forward = list(range(COUNT))
        backward = list(reversed(forward))
        jumpy = [0, COUNT - 1, 0, COUNT // 2, 1, COUNT - 2]
        for requests in (forward, backward, jumpy):
            with self.subTest(direction=requests[:3]):
                self._check(requests, view.view(self.data, requests, BUDGET))

    def test_repeated_requests_are_stable(self):
        requests = [17] * 5 + [3, 17, 3, 3]
        self._check(requests, view.view(self.data, requests, BUDGET))

    def test_loop_boundaries_start_from_transparent_canvas(self):
        # Playing the second loop from frame 0 must reproduce the first loop.
        requests = list(range(COUNT)) + list(range(COUNT))
        result = view.view(self.data, requests, BUDGET)
        self._check(requests, result)
        first = [f["rgba_sha256"] for f in result["frames"][:COUNT]]
        second = [f["rgba_sha256"] for f in result["frames"][COUNT:]]
        self.assertEqual(first, second)


class RandomizedModelTest(unittest.TestCase):
    """Random small animations (random rects/disposal/blend) vs baseline."""

    def test_random_animations_match_reference(self):
        rng = random.Random(31337)
        for case in range(12):
            width = rng.randint(2, 6)
            height = rng.randint(2, 5)
            frames = []
            for _ in range(rng.randint(1, 9)):
                fw = rng.randint(1, width)
                fh = rng.randint(1, height)
                x = rng.randint(0, width - fw)
                y = rng.randint(0, height - fh)
                color = (rng.randrange(256), rng.randrange(256),
                         rng.randrange(256), rng.choice((0, 1, 128, 254, 255)))

                def pixel(px, py, color=color):
                    return bytes((color[0], color[1], color[2], color[3]))

                frames.append({
                    "x": x, "y": y, "w": fw, "h": fh, "pixels": pixel,
                    "delay_num": rng.randint(1, 5),
                    "delay_den": rng.choice((0, 1, 10, 25, 100)),
                    "dispose": rng.choice((0, 1, 2)),
                    "blend": rng.choice((0, 1)),
                })
            data = fixtures.build(width, height, frames)
            expected = [hashlib.sha256(fixtures.rgba_bytes(rows)).hexdigest()
                        for rows in fixtures.reference_frames(width, height, frames)]
            starts = fixtures.starts(frames)
            for budget in (0, 1, 4):
                with self.subTest(case=case, budget=budget):
                    requests = list(range(len(frames)))
                    rng.shuffle(requests)
                    result = view.view(data, requests, budget)
                    for entry in result["frames"]:
                        index = entry["index"]
                        self.assertEqual(entry["rgba_sha256"], expected[index])
                        start = starts[index]
                        self.assertEqual(entry["start"],
                                         [start.numerator, start.denominator])
                    self.assertLessEqual(result["stats"]["max_checkpoints"],
                                         budget)


class BudgetTest(unittest.TestCase):
    """The checkpoint budget bounds residency and single-request replay."""

    def setUp(self):
        self.frames = _fixture_frames()
        self.data = fixtures.build(WIDTH, HEIGHT, self.frames)

    def test_budget_caps_checkpoints_and_replay(self):
        result = view.view(self.data, None, BUDGET)
        stats = result["stats"]
        self.assertEqual(stats["max_checkpoints"], BUDGET)
        self.assertLessEqual(stats["max_replay_frames"], 32)
        self.assertLessEqual(stats["max_checkpoints"], BUDGET)
        # Frame durations stay correct even though replay intervals change.
        rng = random.Random(99)
        requests = [rng.randrange(COUNT) for _ in range(200)]
        random_result = view.view(self.data, requests, BUDGET)
        self.assertLessEqual(random_result["stats"]["max_replay_frames"], 32)
        self.assertLessEqual(random_result["stats"]["max_checkpoints"], BUDGET)

    def test_checkpoint_indices_respect_budget(self):
        for budget in range(0, 9):
            indices = view.checkpoint_indices(COUNT, budget)
            self.assertLessEqual(len(indices), budget)
            self.assertEqual(len(indices), len(set(indices)))
            self.assertEqual(indices, sorted(indices))
            for index in indices:
                self.assertTrue(0 <= index < COUNT - 1)

    def test_checkpoints_store_post_disposal_state(self):
        # Only post-disposal canvases may be retained, never display frames.
        image = view.parse(self.data)
        indices = view.checkpoint_indices(COUNT, BUDGET)
        checkpoints = view.build_checkpoints(image, indices)
        self.assertEqual(len(checkpoints), BUDGET)
        displays, after = fixtures.reference_frames(WIDTH, HEIGHT,
                                                    self.frames, with_after=True)
        disposed = 0
        for index, canvas in checkpoints.items():
            self.assertEqual(hashlib.sha256(canvas).hexdigest(),
                             hashlib.sha256(
                                 fixtures.rgba_bytes(after[index])).hexdigest(),
                             f"checkpoint {index} is not post-disposal")
            self.assertEqual(canvas, bytes(canvas),
                             "checkpoints must stay immutable")
            if self.frames[index]["dispose"] != 0:
                disposed += 1
                self.assertNotEqual(
                    hashlib.sha256(canvas).hexdigest(),
                    hashlib.sha256(
                        fixtures.rgba_bytes(displays[index])).hexdigest(),
                    f"checkpoint {index} holds a display frame")
        self.assertGreater(disposed, 0, "fixture must dispose some checkpoints")

    def test_canvas_residency_peak(self):
        rng = random.Random(5)
        requests = [rng.randrange(COUNT) for _ in range(120)]
        result = view.view(self.data, requests, BUDGET)
        stats = view.canvas_stats()
        self.assertEqual(result["stats"]["max_checkpoints"], BUDGET)
        self.assertEqual(stats["live"], 0, "no canvas may stay resident")
        # Retained checkpoints plus the working canvas and its scratch state.
        self.assertLessEqual(stats["peak"], BUDGET + 2)
        self.assertGreaterEqual(stats["peak"], BUDGET)

    def test_full_playback_never_retains_every_display_frame(self):
        # Sequential playback of all 120 frames must not keep 120 canvases:
        # only the budgeted checkpoints may persist.
        result = view.view(self.data, None, BUDGET)
        self.assertEqual(len(result["frames"]), COUNT)
        self.assertLess(view.canvas_stats()["peak"], COUNT / 4)
        self.assertEqual(view.canvas_stats()["live"], 0)

    def test_residency_scales_with_budget(self):
        for budget in (0, 1, 2, 4, 8):
            with self.subTest(budget=budget):
                result = view.view(self.data, None, budget)
                peak = view.canvas_stats()["peak"]
                self.assertLessEqual(peak, budget + 2)
                self.assertEqual(result["stats"]["max_checkpoints"], budget)
                self.assertEqual(view.canvas_stats()["live"], 0)

    def test_checkpoint_budget_keeps_hashes(self):
        baseline = view.view(self.data, None, BUDGET)
        for budget in (0, 1, 2, 3, 5, 16):
            with self.subTest(budget=budget):
                other = view.view(self.data, None, budget)
                self.assertEqual([f["rgba_sha256"] for f in other["frames"]],
                                 [f["rgba_sha256"] for f in baseline["frames"]])


class ValidationTest(unittest.TestCase):
    """Invalid animation control and frame data must fail with an error."""

    def _run(self, data, request=None, budget=BUDGET):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "in.png"
            target = Path(tmp) / "out.json"
            source.write_bytes(data)
            command = [sys.executable, str(ROOT / "view.py"), str(source),
                       str(target), "--budget", str(budget)]
            if request is not None:
                request_path = Path(tmp) / "req.json"
                request_path.write_text(json.dumps({"frames": request}))
                command += ["--request", str(request_path)]
            process = subprocess.run(command, capture_output=True, text=True)
            return process.returncode, process.stderr.strip()

    def test_out_of_bounds_rectangle(self):
        frames = _fixture_frames()[:4]
        frames[2] = dict(frames[2], x=WIDTH - 1, w=3)
        code, error = self._run(fixtures.build(WIDTH, HEIGHT, frames))
        self.assertEqual(code, 2)
        self.assertIn("rectangle", error)

    def test_duplicate_frame_sequence(self):
        frames = _fixture_frames()[:4]
        data = fixtures.build(WIDTH, HEIGHT, frames)
        broken = data.replace(
            fixtures.fctl(3, frames[2]["w"], frames[2]["h"], frames[2]["x"],
                          frames[2]["y"], frames[2]["delay_num"],
                          frames[2]["delay_den"], frames[2]["dispose"],
                          frames[2]["blend"]),
            fixtures.fctl(2, frames[2]["w"], frames[2]["h"], frames[2]["x"],
                          frames[2]["y"], frames[2]["delay_num"],
                          frames[2]["delay_den"], frames[2]["dispose"],
                          frames[2]["blend"]),
            1)
        self.assertNotEqual(broken, data)
        code, error = self._run(broken)
        self.assertEqual(code, 2)
        self.assertIn("sequence", error)

    def test_truncated_data(self):
        data = fixtures.build(WIDTH, HEIGHT, _fixture_frames()[:4])
        for cut in (len(data) - 8, len(data) // 2, len(data) // 3):
            code, error = self._run(data[:cut])
            self.assertEqual(code, 2)
            self.assertIn("truncated", error)

    def test_truncated_fdat_stream(self):
        frames = _fixture_frames()[:3]
        data = fixtures.build(WIDTH, HEIGHT, frames, default_image=True)
        marker = fixtures.compress(frames[2]["w"], frames[2]["h"],
                                   frames[2]["pixels"])
        self.assertIn(marker, data)
        broken = data.replace(marker, marker[:len(marker) // 2])
        code, error = self._run(broken)
        self.assertEqual(code, 2)
        self.assertIn("truncated", error)

    def test_out_of_range_request(self):
        code, error = self._run(_fixture_png(), request=[COUNT])
        self.assertEqual(code, 2)
        self.assertIn("out of range", error)

    def test_errors_point_at_an_offset(self):
        frames = _fixture_frames()[:4]
        frames[1] = dict(frames[1], y=HEIGHT - 1, h=2)
        code, error = self._run(fixtures.build(WIDTH, HEIGHT, frames))
        self.assertEqual(code, 2)
        self.assertRegex(error, r"rectangle: frame at \d+")

    def test_still_png_uses_zero_duration(self):
        png = bytearray(fixtures.SIGNATURE)
        png += fixtures.chunk(b"IHDR", struct.pack(
            ">IIBBBBB", WIDTH, HEIGHT, 8, 6, 0, 0, 0))
        png += fixtures.chunk(b"IDAT", fixtures.compress(
            WIDTH, HEIGHT, lambda x, y: bytes((x * 8, y * 8, 0, 255))))
        png += fixtures.chunk(b"IEND", b"")
        result = view.view(bytes(png), None, BUDGET)
        self.assertEqual(len(result["frames"]), 1)
        self.assertEqual(result["frames"][0]["duration"], [0, 1])
        self.assertEqual(result["frames"][0]["start"], [0, 1])
        expected = hashlib.sha256(fixtures.rgba_bytes([
            [(x * 8, y * 8, 0, 255) for x in range(WIDTH)]
            for y in range(HEIGHT)])).hexdigest()
        self.assertEqual(result["frames"][0]["rgba_sha256"], expected)
        self.assertEqual(result["stats"]["max_checkpoints"], 0)

    def test_two_actl_chunks_are_rejected(self):
        data = _fixture_png()
        body = fixtures.chunk(b"acTL", struct.pack(">II", COUNT, 0))
        self.assertEqual(data.count(body), 1)
        broken = data.replace(body, body + body, 1)
        code, error = self._run(broken)
        self.assertEqual(code, 2)
        self.assertIn("sequence", error)

    def test_frame_count_mismatch_is_rejected(self):
        data = _fixture_png()
        body = fixtures.chunk(b"acTL", struct.pack(">II", COUNT, 0))
        broken = data.replace(body, fixtures.chunk(
            b"acTL", struct.pack(">II", COUNT - 1, 0)), 1)
        code, error = self._run(broken)
        self.assertEqual(code, 2)
        self.assertIn("sequence", error)

    def test_zero_sized_frame_rectangle_is_rejected(self):
        frames = _fixture_frames()[:3]
        frames[1] = dict(frames[1], w=0)
        code, error = self._run(fixtures.build(WIDTH, HEIGHT, frames))
        self.assertEqual(code, 2)
        self.assertIn("rectangle", error)


if __name__ == "__main__":
    unittest.main()
