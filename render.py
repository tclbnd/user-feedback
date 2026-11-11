#!/usr/bin/env python3
"""Command-line software OBJ renderer (deterministic, tile-based).

Usage:
    python3 render.py input.obj output.png [options]

Options:
    --size N            square output size in pixels (default 512)
    --fov DEGREES       vertical field of view (default 45)
    --distance UNITS    camera distance along +Z (default 5)
    --near UNITS        near clip distance (default 0.25)
    --far UNITS         far clip distance (default 500)
    --light X,Y,Z       direction towards the light (default 0.45,0.8,1)
    --samples {1,4}     sub-samples per pixel (default 1)
    --tile-size N       rasterization tile size in pixels (default 64)
    --workers N         worker processes for tile rasterization (default 1)
    --texture           shade with a procedural checker texture (needs vt)
    --depth-png PATH    also write a grayscale depth visualization
    --no-cull           disable backface culling
"""

from __future__ import annotations

import argparse
import sys

import obj_loader
import png_writer
import rasterizer
import texture as texture_mod


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("expected an integer, got %r" % text)
    if value <= 0:
        raise argparse.ArgumentTypeError("size must be positive")
    return value


def _fov(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("expected a number, got %r" % text)
    if not 0.0 < value < 180.0:
        raise argparse.ArgumentTypeError("fov must be between 0 and 180 degrees")
    return value


def _distance(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("expected a number, got %r" % text)
    if value <= 0.0:
        raise argparse.ArgumentTypeError("distance must be positive")
    return value


def _light(text: str):
    parts = text.replace(",", " ").split()
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("light must be 'X,Y,Z'")
    try:
        vector = tuple(float(part) for part in parts)
    except ValueError:
        raise argparse.ArgumentTypeError("light components must be numbers")
    if all(component == 0.0 for component in vector):
        raise argparse.ArgumentTypeError("light direction must be non-zero")
    return vector


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a Wavefront OBJ mesh to a PNG using a "
                    "deterministic tiled software rasterizer."
    )
    parser.add_argument("input", help="input .obj file")
    parser.add_argument("output", help="output .png file")
    parser.add_argument("--size", type=_positive_int, default=512,
                        help="square image size in pixels (default: 512)")
    parser.add_argument("--fov", type=_fov, default=45.0,
                        help="vertical field of view in degrees (default: 45)")
    parser.add_argument("--distance", type=_distance, default=5.0,
                        help="camera distance along +Z (default: 5)")
    parser.add_argument("--near", type=_distance, default=0.25,
                        help="near clip distance (default: 0.25)")
    parser.add_argument("--far", type=_distance, default=500.0,
                        help="far clip distance (default: 500)")
    parser.add_argument("--light", type=_light, default=(0.45, 0.8, 1.0),
                        help="direction towards the light as X,Y,Z "
                             "(default: 0.45,0.8,1)")
    parser.add_argument("--samples", type=int, choices=(1, 4), default=1,
                        help="sub-samples per pixel: 1 or 4 (default: 1)")
    parser.add_argument("--tile-size", type=_positive_int, default=64,
                        help="rasterization tile size in pixels (default: 64)")
    parser.add_argument("--workers", type=_positive_int, default=1,
                        help="worker processes for tiles (default: 1)")
    parser.add_argument("--texture", action="store_true",
                        help="shade with a procedural checker texture "
                             "using the mesh's vt coordinates")
    parser.add_argument("--depth-png", metavar="PATH",
                        help="also write a grayscale depth visualization")
    parser.add_argument("--no-cull", action="store_true",
                        help="disable backface culling")
    return parser


def _depth_rows(result: rasterizer.RenderResult):
    """Normalize the depth channel to 0-255 grayscale rows."""
    if result.depth_min is None:
        return [[(0, 0, 0)] * result.width for _ in range(result.height)]
    lo, hi = result.depth_min, result.depth_max
    span = (hi - lo) or 1.0
    rows = []
    for y in range(result.height):
        row = []
        for x in range(result.width):
            if result.coverage[y][x] == 0:
                row.append((0, 0, 0))
            else:
                gray = int(round(255.0 * (result.depth[y][x] - lo) / span))
                row.append((gray, gray, gray))
        rows.append(row)
    return rows


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.near >= args.far:
        print("error: --near must be smaller than --far", file=sys.stderr)
        return 1

    try:
        mesh = obj_loader.load_mesh(args.input)
    except (OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    if not mesh.faces:
        print("error: %s contains no renderable faces" % args.input, file=sys.stderr)
        return 1
    if args.texture and not mesh.uvs:
        print("error: %s has no vt coordinates for --texture" % args.input,
              file=sys.stderr)
        return 1

    result = rasterizer.render(
        mesh.positions,
        mesh.uvs,
        mesh.faces,
        size=args.size,
        fov_deg=args.fov,
        distance=args.distance,
        near=args.near,
        far=args.far,
        samples=args.samples,
        tile_size=args.tile_size,
        workers=args.workers,
        cull_backfaces=not args.no_cull,
        light_dir=args.light,
        texture=texture_mod.DEFAULT_CHECKER if args.texture else None,
    )

    try:
        png_writer.write_png(
            args.output, result.width, result.height, result.pixels
        )
        if args.depth_png:
            png_writer.write_png(
                args.depth_png, result.width, result.height,
                _depth_rows(result),
            )
    except OSError as exc:
        print("error: cannot write output: %s" % exc, file=sys.stderr)
        return 1

    if result.depth_min is None:
        depth_text = "n/a"
    else:
        depth_text = "%.4f .. %.4f" % (result.depth_min, result.depth_max)

    print("triangle count: %d" % result.triangle_count)
    print("triangles rasterized: %d" % result.triangles_drawn)
    print("foreground pixels: %d" % result.foreground_pixels)
    print("foreground ratio: %.2f%%" % (result.foreground_ratio * 100.0))
    print("depth range: %s" % depth_text)
    print("resolution: %dx%d (samples: %dx, tile: %d, workers: %d)"
          % (result.width, result.height, result.samples,
             result.tile_size, args.workers))
    print("digest: %s" % result.digest())
    print("output: %s" % args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
