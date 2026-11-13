"""Vector rasterizer front end.

Usage (unchanged signature):  python3 vectors.py demo.json output.json --tile 4

The engine lives in the rsample package: curve flattening, nonzero/evenodd
rules, implicit closure, multi-layer clips, 4x4 per-sample coverage and
premultiplied compositing. tile only partitions the work; the same scene with
different tile sizes renders pixel-identical output.
"""
import argparse
import json
from pathlib import Path

from rsample.coverage import render, rasterize_reference, rasterize_tiled
from rsample.imageio import write_coverage_pgm, write_rgba_ppm


def polygon(commands):
    """Backward-compatible helper: anchor points of the old straight baseline."""
    return [tuple(command[-2:]) for command in commands
            if command[0] in ("M", "L", "Q", "C")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--tile", type=int, default=8)
    parser.add_argument("--reference", action="store_true",
                        help="use the independent per-sample winding engine")
    parser.add_argument("--pgm", type=Path,
                        help="optional coverage .pgm image output")
    parser.add_argument("--ppm", type=Path,
                        help="optional white-flattened RGBA .ppm image output")
    args = parser.parse_args()

    scene = json.loads(args.input.read_text())
    engine = rasterize_reference if args.reference else rasterize_tiled
    result = render(scene, tile=args.tile, rasterizer=engine)
    args.output.write_text(json.dumps(result, sort_keys=True) + "\n")

    if args.pgm:
        write_coverage_pgm(str(args.pgm), result["mask"])
    if args.ppm:
        write_rgba_ppm(str(args.ppm), result["rgba"])


if __name__ == "__main__":
    main()
