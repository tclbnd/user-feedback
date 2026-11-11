#!/usr/bin/env python3
"""Generate the committed golden images and buffer digests.

Run from the project root:

    python3 tools/generate_goldens.py

The output is deterministic for this renderer, so regenerating after an
intentional renderer change should produce a reviewable diff.
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import metrics  # noqa: E402
import obj_loader  # noqa: E402
import png_writer  # noqa: E402
import rasterizer  # noqa: E402
import scenes  # noqa: E402

GOLDEN_DIR = os.path.join(ROOT, "tests", "golden")

CUBE_SIZE = 512
SCENE_SIZE = 128
TILE_SIZE = 32
WORKERS = 1
TILE_ORDER = "row"


def _render_cube():
    triangles = obj_loader.load_obj_scene(os.path.join(ROOT, "assets", "cube.obj"))
    result = rasterizer.render(
        triangles,
        size=CUBE_SIZE,
        samples=1,
        tile_size=TILE_SIZE,
        workers=WORKERS,
        tile_order=TILE_ORDER,
        collect_uv=False,
    )
    png_writer.write_png(
        os.path.join(GOLDEN_DIR, "cube.png"),
        result.width, result.height, result.pixels,
    )
    return result


def _render_scene(scene, samples):
    result = rasterizer.render(
        scene.triangles,
        size=SCENE_SIZE,
        cull_backfaces=scene.cull_backfaces,
        samples=samples,
        tile_size=TILE_SIZE,
        workers=WORKERS,
        tile_order=TILE_ORDER,
        collect_uv=True,
    )
    name = "%s_s%d.png" % (scene.name, samples)
    png_writer.write_png(
        os.path.join(GOLDEN_DIR, name),
        result.width, result.height, result.pixels,
    )
    digest = metrics.buffer_digests(result)
    return {
        "digests": digest,
        "foreground_pixels": result.foreground_pixels,
        "triangles_drawn": result.triangles_drawn,
        "depth_min": result.depth_min,
        "depth_max": result.depth_max,
        "coverage_bits": sorted({
            mask for row in result.coverage for mask in row
        }),
    }


def main() -> int:
    os.makedirs(GOLDEN_DIR, exist_ok=True)

    cube = _render_cube()
    cube_entry = {
        "digests": metrics.buffer_digests(cube),
        "foreground_pixels": cube.foreground_pixels,
        "triangles_drawn": cube.triangles_drawn,
        "depth_min": cube.depth_min,
        "depth_max": cube.depth_max,
        "triangle_count": cube.triangle_count,
    }

    scene_entries = {}
    for scene in scenes.regression_scenes():
        scene_entries[scene.name] = {
            "description": scene.description,
            "cull_backfaces": scene.cull_backfaces,
            "s1": _render_scene(scene, 1),
            "s4": _render_scene(scene, 4),
        }

    payload = {
        "config": {
            "cube_size": CUBE_SIZE,
            "scene_size": SCENE_SIZE,
            "tile_size": TILE_SIZE,
            "workers": WORKERS,
            "tile_order": TILE_ORDER,
        },
        "cube": cube_entry,
        "scenes": scene_entries,
    }
    with open(os.path.join(GOLDEN_DIR, "digests.json"), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")

    print("wrote golden files to %s" % GOLDEN_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
