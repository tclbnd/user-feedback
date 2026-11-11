#!/usr/bin/env python3
"""Command-line software OBJ renderer.

Usage:
    python3 render.py input.obj output.png [--size N]

Options:
    --size N            square output size in pixels (default 512)
    --fov DEGREES       vertical field of view (default 45)
    --distance UNITS    camera distance along +Z (default 5)
    --light X,Y,Z       direction towards the light (default 0.45,0.8,1)
    --no-cull           disable backface culling
"""

from __future__ import annotations

import argparse
import sys

import obj_loader
import png_writer
import rasterizer


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
        description="Render a Wavefront OBJ mesh to a PNG using a software rasterizer."
    )
    parser.add_argument("input", help="input .obj file")
    parser.add_argument("output", help="output .png file")
    parser.add_argument("--size", type=_positive_int, default=512,
                        help="square image size in pixels (default: 512)")
    parser.add_argument("--fov", type=_fov, default=45.0,
                        help="vertical field of view in degrees (default: 45)")
    parser.add_argument("--distance", type=_distance, default=5.0,
                        help="camera distance along +Z (default: 5)")
    parser.add_argument("--light", type=_light, default=(0.45, 0.8, 1.0),
                        help="direction towards the light as X,Y,Z "
                             "(default: 0.45,0.8,1)")
    parser.add_argument("--no-cull", action="store_true",
                        help="disable backface culling")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    try:
        triangles = obj_loader.load_obj(args.input)
    except (OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    if not triangles:
        print("error: %s contains no renderable faces" % args.input, file=sys.stderr)
        return 1

    result = rasterizer.render(
        triangles,
        size=args.size,
        fov_deg=args.fov,
        distance=args.distance,
        cull_backfaces=not args.no_cull,
        light_dir=args.light,
    )

    try:
        png_writer.write_png(
            args.output, result.width, result.height, result.pixels
        )
    except OSError as exc:
        print("error: cannot write %s: %s" % (args.output, exc), file=sys.stderr)
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
    print("resolution: %dx%d" % (result.width, result.height))
    print("output: %s" % args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
