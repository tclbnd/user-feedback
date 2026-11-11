#!/usr/bin/env python3
"""One-click local acceptance for the deterministic renderer.

Run from the project root:

    python3 acceptance.py

It checks, in order:

1. the base cube regression and the ``render.py`` PNG entry point;
2. pixel-exact golden regressions for the new scenes (*1x* and *4x*);
3. an independent ray-cast reference for coverage, depth and UV values;
4. single-owner shared edges and crack-free frustum clipping;
5. output equality across tile sizes, tile execution orders and worker counts;
6. large-scene runtime and peak Python allocation per tile size.

Exit status is 0 only if every check passes.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import subprocess
import sys
import time
import tracemalloc
from typing import Dict, List, Tuple

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import metrics  # noqa: E402
import obj_loader  # noqa: E402
import png_reader  # noqa: E402
import png_writer  # noqa: E402
import rasterizer  # noqa: E402
import reference  # noqa: E402
import scenes  # noqa: E402
from geometry import Triangle  # noqa: E402

GOLDEN_DIR = os.path.join(ROOT, "tests", "golden")
BUILD_DIR = os.path.join(ROOT, "build")


class Acceptance:
    def __init__(self) -> None:
        self.failures: List[str] = []
        self.lines: List[str] = []

    def section(self, title: str) -> None:
        self.lines.append("")
        self.lines.append("== %s ==" % title)

    def info(self, text: str) -> None:
        self.lines.append(text)

    def check(self, condition: bool, message: str) -> bool:
        if condition:
            self.lines.append("  PASS  %s" % message)
        else:
            self.lines.append("  FAIL  %s" % message)
            self.failures.append(message)
        return condition

    def fail(self, message: str) -> None:
        self.lines.append("  FAIL  %s" % message)
        self.failures.append(message)


def _short(digest: str) -> str:
    return digest[:12]


def _load_digests() -> dict:
    with open(os.path.join(GOLDEN_DIR, "digests.json"), "r", encoding="utf-8") as handle:
        return json.load(handle)


def _render_scene(scene, size, samples, tile_size, workers=1,
                  tile_order="row", collect_uv=True):
    return rasterizer.render(
        scene.triangles,
        size=size,
        cull_backfaces=scene.cull_backfaces,
        samples=samples,
        tile_size=tile_size,
        workers=workers,
        tile_order=tile_order,
        collect_uv=collect_uv,
    )


def _check_valid_depths(result, label: str, acceptance: Acceptance) -> None:
    bad = 0
    minimum = math.inf
    maximum = -math.inf
    covered = 0
    max_mask = (1 << result.samples) - 1
    for y in range(result.height):
        for x in range(result.width):
            mask = result.coverage[y][x]
            if mask & ~max_mask:
                bad += 1
                continue
            depth = result.depth_values[y][x]
            if mask == 0:
                if depth is not None:
                    bad += 1
                continue
            covered += 1
            if depth is None or not math.isfinite(depth):
                bad += 1
                continue
            if depth < result.near - 1e-12 or depth > result.far + 1e-9:
                bad += 1
                continue
            minimum = min(minimum, depth)
            maximum = max(maximum, depth)
    acceptance.check(
        bad == 0,
        "%s: %d covered pixels have finite depths in [near, far] "
        "(%.6f .. %.6f)" % (label, covered, minimum, maximum),
    )


# ---------------------------------------------------------------------------
# 1. Cube regression and CLI


def run_cube(acceptance: Acceptance, digests: dict) -> None:
    acceptance.section("1. Cube regression")

    golden_width, golden_height, golden_rows = png_reader.decode_png(
        os.path.join(GOLDEN_DIR, "cube.png")
    )

    triangles = obj_loader.load_obj_scene(os.path.join(ROOT, "assets", "cube.obj"))
    result = rasterizer.render(
        triangles, size=512, samples=1, tile_size=32, workers=1,
        tile_order="row", collect_uv=False,
    )

    acceptance.check(
        (result.width, result.height) == (512, 512)
        and (golden_width, golden_height) == (512, 512),
        "golden and rendered cube are 512x512",
    )
    acceptance.check(result.pixels == golden_rows,
                     "cube pixels match committed golden image exactly")
    acceptance.check(result.triangle_count == 12,
                     "triangle count is 12")
    acceptance.check(result.triangles_drawn == 4,
                     "triangles rasterized is 4")
    acceptance.check(result.foreground_pixels == 96100,
                     "foreground pixels is 96100")
    acceptance.check(abs(result.foreground_ratio - 0.3666) < 5e-5,
                     "foreground ratio is 36.66%")
    acceptance.check(
        result.depth_min is not None
        and result.depth_max is not None
        and abs(result.depth_min - 4.0) < 1e-9
        and abs(result.depth_max - 4.0) < 1e-9,
        "depth range is 4.0000 .. 4.0000",
    )
    acceptance.check(
        metrics.buffer_digests(result) == digests["cube"]["digests"],
        "cube buffer digests match golden digests",
    )

    # Exercise the actual CLI/PNG entry point in a subprocess.
    os.makedirs(BUILD_DIR, exist_ok=True)
    cli_output = os.path.join(BUILD_DIR, "cube_cli.png")
    process = subprocess.run(
        [sys.executable, os.path.join(ROOT, "render.py"),
         os.path.join(ROOT, "assets", "cube.obj"), cli_output],
        cwd=ROOT, capture_output=True, text=True,
    )
    acceptance.check(process.returncode == 0,
                     "render.py exits successfully")
    expected_lines = [
        "triangle count: 12",
        "triangles rasterized: 4",
        "foreground pixels: 96100",
        "foreground ratio: 36.66%",
        "depth range: 4.0000 .. 4.0000",
        "resolution: 512x512",
    ]
    missing = [line for line in expected_lines if line not in process.stdout]
    acceptance.check(not missing,
                     "render.py prints the expected cube summary")
    if os.path.exists(cli_output):
        cli_width, cli_height, cli_rows = png_reader.decode_png(cli_output)
        acceptance.check(
            (cli_width, cli_height) == (512, 512)
            and cli_rows == golden_rows,
            "render.py PNG output decodes to the golden cube pixels",
        )
    else:
        acceptance.fail("render.py did not write its PNG output")
        acceptance.fail(process.stderr.strip() or "no stderr")


# ---------------------------------------------------------------------------
# 2. Pixel-level golden regression


def run_regression(acceptance: Acceptance, digests: dict) -> None:
    acceptance.section("2. Pixel-level golden regression (128x128)")

    config = digests["config"]
    size = config["scene_size"]
    tile_size = config["tile_size"]

    for scene in scenes.regression_scenes():
        entry = digests["scenes"][scene.name]
        for samples in (1, 4):
            result = _render_scene(scene, size, samples, tile_size,
                                   workers=config["workers"],
                                   tile_order=config["tile_order"],
                                   collect_uv=True)
            golden_path = os.path.join(
                GOLDEN_DIR, "%s_s%d.png" % (scene.name, samples)
            )
            golden_width, golden_height, golden_rows = png_reader.decode_png(
                golden_path
            )
            pixels_equal = (
                (result.width, result.height) == (golden_width, golden_height)
                and result.pixels == golden_rows
            )
            acceptance.check(
                pixels_equal,
                "%s %dx: pixels match golden" % (scene.name, samples),
            )
            actual = metrics.buffer_digests(result)
            expected = entry["s%d" % samples]["digests"]
            acceptance.check(
                actual == expected,
                "%s %dx: colour/depth/coverage/UV/primitive digests match"
                % (scene.name, samples),
            )
            _check_valid_depths(result, "%s %dx" % (scene.name, samples),
                                acceptance)

    # The two triangulations of the same coplanar quad must be identical.
    fan = _render_scene(scenes.coplanar_quad("02"), size, 1, tile_size, collect_uv=True)
    alternate = _render_scene(scenes.coplanar_quad("13"), size, 1, tile_size, collect_uv=True)
    acceptance.check(
        fan.pixels == alternate.pixels,
        "coplanar quad: diagonal 02 and diagonal 13 render identical colours",
    )
    max_depth_difference = 0.0
    for y in range(size):
        for x in range(size):
            a = fan.depth_values[y][x]
            b = alternate.depth_values[y][x]
            if a is None or b is None:
                if a is not None or b is not None:
                    max_depth_difference = math.inf
                continue
            max_depth_difference = max(max_depth_difference, abs(a - b))
    acceptance.check(
        max_depth_difference <= 1e-9,
        "coplanar quad: both triangulations produce the same depths "
        "(max difference %.3e)" % max_depth_difference,
    )
    acceptance.check(
        fan.coverage == alternate.coverage,
        "coplanar quad: both triangulations produce identical coverage",
    )


# ---------------------------------------------------------------------------
# 2b. OBJ attribute path


def _write_textured_obj(path: str, triangles) -> None:
    with open(path, "w", encoding="ascii") as handle:
        handle.write("# deterministic textured triangles\n")
        position_index = 1
        uv_index = 1
        for triangle in triangles:
            if (triangle.uv0 is None or triangle.uv1 is None
                    or triangle.uv2 is None):
                continue
            points = (triangle.p0, triangle.p1, triangle.p2)
            uvs = (triangle.uv0, triangle.uv1, triangle.uv2)
            for point in points:
                handle.write("v %.17g %.17g %.17g\n" % point)
            for uv in uvs:
                handle.write("vt %.17g %.17g\n" % uv)
            handle.write(
                "f %d/%d %d/%d %d/%d\n" % (
                    position_index, uv_index,
                    position_index + 1, uv_index + 1,
                    position_index + 2, uv_index + 2,
                )
            )
            position_index += 3
            uv_index += 3


def run_obj_uv(acceptance: Acceptance) -> None:
    acceptance.section("2b. OBJ input with texture coordinates")

    scene = scenes.near_clip_textured()
    os.makedirs(BUILD_DIR, exist_ok=True)
    path = os.path.join(BUILD_DIR, "near_clip_textured.obj")
    _write_textured_obj(path, scene.triangles)
    loaded = obj_loader.load_obj_scene(path)

    acceptance.check(
        len(loaded) == len(scene.triangles),
        "textured OBJ loads %d triangles" % len(loaded),
    )
    same_geometry = True
    for actual, expected in zip(loaded, scene.triangles):
        if (actual.p0, actual.p1, actual.p2) != (
            expected.p0, expected.p1, expected.p2
        ):
            same_geometry = False
        if (actual.uv0, actual.uv1, actual.uv2) != (
            expected.uv0, expected.uv1, expected.uv2
        ):
            same_geometry = False
    acceptance.check(
        same_geometry,
        "textured OBJ round-trips positions and UVs exactly",
    )

    for samples in (1, 4):
        direct = rasterizer.render(
            scene.triangles, size=64, cull_backfaces=scene.cull_backfaces,
            samples=samples, tile_size=16, collect_uv=True,
        )
        from_obj = rasterizer.render(
            loaded, size=64, cull_backfaces=scene.cull_backfaces,
            samples=samples, tile_size=16, collect_uv=True,
        )
        acceptance.check(
            metrics.buffer_digests(direct) == metrics.buffer_digests(from_obj),
            "OBJ-loaded textured mesh %dx matches the in-memory scene"
            % samples,
        )


# ---------------------------------------------------------------------------
# 3. Independent ray reference


def _select_points(result, limit: int = 12):
    covered = [
        (x, y) for y in range(result.height) for x in range(result.width)
        if result.coverage[y][x] != 0
    ]
    if not covered:
        return []
    step = max(1, len(covered) // limit)
    return covered[::step][:limit]


def run_reference(acceptance: Acceptance) -> None:
    acceptance.section("3. Independent ray-cast reference (32x32)")

    size = 32
    for scene in scenes.reference_scenes():
        for samples in (1, 4):
            result = rasterizer.render(
                scene.triangles, size=size,
                cull_backfaces=scene.cull_backfaces,
                samples=samples, tile_size=8, tile_order="row",
                collect_uv=True,
            )
            ref = reference.render_reference(
                scene.triangles, size, size, samples=samples,
            )

            coverage_mismatch = 0
            depth_error = 0.0
            uv_error = 0.0
            compared = 0
            selected: List[Tuple[int, int, float, Tuple[float, float]]] = []

            for y in range(size):
                for x in range(size):
                    if result.coverage[y][x] != ref.coverage[y][x]:
                        coverage_mismatch += 1
                        continue
                    rd = result.depth_values[y][x]
                    fd = ref.depth[y][x]
                    if rd is None or fd is None:
                        if rd is not None or fd is not None:
                            coverage_mismatch += 1
                        continue
                    depth_error = max(depth_error, abs(rd - fd))
                    ru = result.uv_values[y][x]
                    fu = ref.uv[y][x]
                    if ru is not None and fu is not None:
                        uv_error = max(
                            uv_error,
                            abs(ru[0] - fu[0]), abs(ru[1] - fu[1]),
                        )
                    compared += 1

            acceptance.check(
                coverage_mismatch == 0,
                "%s %dx: coverage masks match the ray reference (%d pixels)"
                % (scene.name, samples, size * size),
            )
            acceptance.check(
                depth_error <= 1e-9,
                "%s %dx: resolved depth error %.3e <= 1e-9 (%d points)"
                % (scene.name, samples, depth_error, compared),
            )
            acceptance.check(
                uv_error <= 1e-9,
                "%s %dx: resolved UV error %.3e <= 1e-9"
                % (scene.name, samples, uv_error),
            )

            for x, y in _select_points(result):
                selected.append(
                    (x, y, result.depth_values[y][x], result.uv_values[y][x])
                )
            sample = selected[0] if selected else None
            if sample is not None:
                depth_text = ("%.6f" % sample[2]) if sample[2] is not None else "None"
                uv_text = ("(%.6f, %.6f)" % sample[3]) if sample[3] is not None else "(None)"
                acceptance.check(
                    sample[2] is not None and sample[3] is not None,
                    "%s %dx: selected point (%d,%d) depth=%s uv=%s"
                    % (scene.name, samples, sample[0], sample[1],
                       depth_text, uv_text),
                )


# ---------------------------------------------------------------------------
# 4. Shared-edge ownership


def run_coedges(acceptance: Acceptance) -> None:
    acceptance.section("4. Shared-edge ownership and clipping")

    coedge_scenes = [
        scenes.coplanar_quad("02"),
        scenes.coplanar_quad("13"),
        scenes.frustum_edge_shared(),
        scenes.mixed_winding(),
    ]
    size = 64
    for scene in coedge_scenes:
        for samples in (1, 4):
            first = rasterizer.render(
                [scene.triangles[0]], size=size,
                cull_backfaces=scene.cull_backfaces,
                samples=samples, tile_size=16, collect_uv=False,
            )
            second = rasterizer.render(
                [scene.triangles[1]], size=size,
                cull_backfaces=scene.cull_backfaces,
                samples=samples, tile_size=16, collect_uv=False,
            )
            combined = rasterizer.render(
                scene.triangles, size=size,
                cull_backfaces=scene.cull_backfaces,
                samples=samples, tile_size=16, collect_uv=False,
            )

            shared_samples = 0
            union_mismatch = 0
            for y in range(size):
                for x in range(size):
                    if first.coverage[y][x] & second.coverage[y][x]:
                        shared_samples += 1
                    if ((first.coverage[y][x] | second.coverage[y][x])
                            != combined.coverage[y][x]):
                        union_mismatch += 1

            acceptance.check(
                shared_samples == 0,
                "%s %dx: no sample is owned by both triangles"
                % (scene.name, samples),
            )
            acceptance.check(
                union_mismatch == 0,
                "%s %dx: per-triangle union equals combined coverage (no cracks)"
                % (scene.name, samples),
            )


# ---------------------------------------------------------------------------
# 4b. Tiny / degenerate triangles and stable depth ties


def run_robustness(acceptance: Acceptance) -> None:
    acceptance.section("4b. Tiny, degenerate and equal-depth primitives")

    size = 64
    tan_half = math.tan(math.radians(45.0) * 0.5)
    pixel_x = pixel_y = 32
    sample_x = pixel_x + 0.5
    sample_y = pixel_y + 0.5
    ndc_x = 2.0 * sample_x / size - 1.0
    ndc_y = 1.0 - 2.0 * sample_y / size
    depth = 4.0
    world_x = ndc_x * depth * tan_half
    world_y = ndc_y * depth * tan_half
    world_z = 5.0 - depth
    epsilon = 1e-6
    tiny = Triangle(
        (world_x - epsilon, world_y - epsilon, world_z),
        (world_x + epsilon, world_y - epsilon, world_z),
        (world_x, world_y + epsilon, world_z),
        (0.0, 0.0), (1.0, 0.0), (0.0, 1.0),
        (200, 60, 60),
    )
    degenerate = Triangle(
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
    )

    tiny_result = rasterizer.render(
        [tiny], size=size, cull_backfaces=False, tile_size=8, collect_uv=True,
    )
    acceptance.check(
        tiny_result.foreground_pixels == 1
        and tiny_result.coverage[pixel_y][pixel_x] == 1,
        "a sub-pixel triangle still covers its pixel-centre sample",
    )
    acceptance.check(
        tiny_result.depth_values[pixel_y][pixel_x] is not None
        and math.isfinite(tiny_result.depth_values[pixel_y][pixel_x])
        and abs(tiny_result.depth_values[pixel_y][pixel_x] - depth) < 1e-5,
        "the tiny triangle reports a finite depth close to %.4f" % depth,
    )

    degenerate_result = rasterizer.render(
        [degenerate], size=size, cull_backfaces=False, tile_size=8,
    )
    acceptance.check(
        degenerate_result.foreground_pixels == 0
        and degenerate_result.triangles_drawn == 0,
        "a zero-area triangle is skipped without producing fragments",
    )

    mixed_result = rasterizer.render(
        [tiny, degenerate], size=size, cull_backfaces=False, tile_size=8,
        collect_uv=True,
    )
    acceptance.check(
        mixed_result.foreground_pixels == 1
        and mixed_result.depth_values[pixel_y][pixel_x] is not None,
        "tiny and degenerate primitives coexist safely",
    )

    # Two exactly coplanar primitives at the same depth: the lower stable
    # primitive id must win, independent of tile execution order.
    red = Triangle(
        (-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 0.0),
        (0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (200, 40, 40),
    )
    blue = Triangle(
        (-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 0.0),
        (0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (40, 80, 220),
    )
    red_only = rasterizer.render([red], size=size, cull_backfaces=False,
                                 tile_size=8)
    blue_only = rasterizer.render([blue], size=size, cull_backfaces=False,
                                  tile_size=8)
    red_first = rasterizer.render([red, blue], size=size,
                                  cull_backfaces=False, tile_size=8)
    blue_first = rasterizer.render([blue, red], size=size,
                                   cull_backfaces=False, tile_size=8,
                                   tile_order="reverse")
    center = (pixel_y, pixel_x)
    acceptance.check(
        red_first.pixels[center[0]][center[1]]
        == red_only.pixels[center[0]][center[1]],
        "equal-depth fragments: lower primitive id wins for [red, blue]",
    )
    acceptance.check(
        blue_first.pixels[center[0]][center[1]]
        == blue_only.pixels[center[0]][center[1]],
        "equal-depth fragments: lower primitive id wins for [blue, red]",
    )
    acceptance.check(
        red_first.primitive_ids[center[0]][center[1]] == 0
        and blue_first.primitive_ids[center[0]][center[1]] == 0,
        "equal-depth fragments report the winning stable primitive id",
    )


# ---------------------------------------------------------------------------
# 5. Determinism


def _digest_tuple(result):
    digests = metrics.buffer_digests(result)
    return tuple(digests[key] for key in
                 ("pixels", "depth", "coverage", "uv", "primitive_ids"))


def _determinism_variants():
    tile_sizes = (8, 16, 32, 64)
    orders = ("row", "reverse", "serpentine", "column", "shuffle")
    variants = [(tile, order, 1) for tile in tile_sizes for order in orders]
    variants.extend([(16, "row", 2), (16, "reverse", 2),
                     (32, "row", 4), (32, "reverse", 4)])
    return variants


def run_determinism(acceptance: Acceptance) -> None:
    acceptance.section("5. Determinism across tiles, orders and workers")

    size = 48
    variants = _determinism_variants()
    for scene in scenes.regression_scenes():
        for samples in (1, 4):
            baseline = None
            unique = set()
            for tile_size, tile_order, workers in variants:
                result = rasterizer.render(
                    scene.triangles, size=size,
                    cull_backfaces=scene.cull_backfaces,
                    samples=samples, tile_size=tile_size, workers=workers,
                    tile_order=tile_order, collect_uv=True,
                )
                digest = _digest_tuple(result)
                unique.add(digest)
                if baseline is None:
                    baseline = digest
            acceptance.check(
                len(unique) == 1,
                "%s %dx: %d execution variants produce 1 unique output "
                "(colour %s, depth %s, coverage %s)"
                % (scene.name, samples, len(variants),
                   _short(baseline[0]), _short(baseline[1]), _short(baseline[2])),
            )

    # Report a concrete execution-order summary for one scene.
    summary_scene = scenes.frustum_edge_shared()
    acceptance.info("  execution-order summary (%s, 4x, 64x64):"
                    % summary_scene.name)
    baseline = None
    for tile_size, tile_order, workers in (
        (32, "row", 1),
        (32, "reverse", 1),
        (32, "serpentine", 1),
        (32, "column", 1),
        (32, "shuffle", 1),
        (16, "row", 2),
        (32, "reverse", 4),
    ):
        result = rasterizer.render(
            summary_scene.triangles, size=64,
            cull_backfaces=summary_scene.cull_backfaces,
            samples=4, tile_size=tile_size, workers=workers,
            tile_order=tile_order, collect_uv=True,
        )
        digest = _digest_tuple(result)
        if baseline is None:
            baseline = digest
        acceptance.info(
            "    tile=%-2d order=%-10s workers=%d  colour=%s depth=%s "
            "coverage=%s  %s"
            % (tile_size, tile_order, workers,
               _short(digest[0]), _short(digest[1]), _short(digest[2]),
               "same" if digest == baseline else "DIFF")
        )
        acceptance.check(digest == baseline,
                         "execution summary variant tile=%d order=%s workers=%d"
                         % (tile_size, tile_order, workers))


# ---------------------------------------------------------------------------
# 6. Large-scene benchmark


def run_benchmark(acceptance: Acceptance) -> None:
    acceptance.section("6. Large-scene benchmark")

    os.makedirs(BUILD_DIR, exist_ok=True)
    cells = 192
    obj_path = os.path.join(BUILD_DIR, "large_grid_%d.obj" % cells)
    scenes.write_grid_obj(obj_path, cells=cells, half_extent=2.2)
    load_start = time.perf_counter()
    triangles = obj_loader.load_obj_scene(obj_path)
    load_time = time.perf_counter() - load_start
    acceptance.info("  OBJ: %s (%d triangles, loaded in %.2fs)"
                    % (os.path.relpath(obj_path, ROOT), len(triangles),
                       load_time))

    size = 256
    tile_sizes = (16, 32, 64, 128)
    digests = []
    acceptance.info("  %-6s %-8s %-8s %-10s %-8s"
                    % ("tile", "time(s)", "peak(MB)", "foreground", "digest"))
    for tile_size in tile_sizes:
        gc.collect()
        start = time.perf_counter()
        result = rasterizer.render(
            triangles, size=size, samples=1, tile_size=tile_size,
            workers=1, tile_order="row", collect_uv=False,
        )
        elapsed = time.perf_counter() - start

        # Measure peak allocation in a second run so tracemalloc's overhead
        # does not inflate the reported wall time.
        gc.collect()
        tracemalloc.start()
        rasterizer.render(
            triangles, size=size, samples=1, tile_size=tile_size,
            workers=1, tile_order="row", collect_uv=False,
        )
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        digest = metrics.buffer_digests(result)
        digests.append(digest)
        acceptance.info(
            "  %-6d %-8.3f %-8.1f %-10d %s"
            % (tile_size, elapsed, peak / 1e6,
               result.foreground_pixels, _short(digest["pixels"]))
        )
        _check_valid_depths(result, "large grid tile=%d" % tile_size, acceptance)

    acceptance.check(
        all(digest == digests[0] for digest in digests),
        "large grid: all tile sizes produce identical colour/depth/coverage",
    )


# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run all local acceptance checks.")
    parser.add_argument("--skip-benchmark", action="store_true",
                        help="skip the large-scene benchmark section")
    args = parser.parse_args(argv)

    acceptance = Acceptance()
    digests = _load_digests()

    run_cube(acceptance, digests)
    run_regression(acceptance, digests)
    run_obj_uv(acceptance)
    run_reference(acceptance)
    run_coedges(acceptance)
    run_robustness(acceptance)
    run_determinism(acceptance)
    if not args.skip_benchmark:
        run_benchmark(acceptance)

    acceptance.section("Summary")
    acceptance.info("  checks failed: %d" % len(acceptance.failures))
    if acceptance.failures:
        for failure in acceptance.failures:
            acceptance.info("    - %s" % failure)
        acceptance.info("ACCEPTANCE: FAIL")
    else:
        acceptance.info("ACCEPTANCE: PASS")

    print("\n".join(acceptance.lines))
    return 0 if not acceptance.failures else 1


if __name__ == "__main__":
    sys.exit(main())
