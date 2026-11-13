"""Command line entry for the offline spectral path replay renderer."""
import argparse
import json
from pathlib import Path

from renderer import render


def _perm(spec: str, tile: int, pixels: int):
    groups = max(1, (pixels + tile - 1) // tile)
    order = [int(x) for x in spec.split(",") if x]
    if sorted(order) != list(range(groups)):
        raise argparse.ArgumentTypeError(
            "tile-order must be a permutation of 0..%d" % (groups - 1))
    return order


def main():
    p = argparse.ArgumentParser(
        description="Spectral path replay with unified estimator weights.")
    p.add_argument("input", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--tile", type=int, default=4,
                   help="pixels per scheduling tile")
    p.add_argument("--workers", type=int, default=1,
                   help="worker threads (draws stay deterministic)")
    p.add_argument("--tile-order", type=str, default=None,
                   help="comma list permuting tile processing order")
    a = p.parse_args()

    config = json.loads(a.input.read_text())
    order = (_perm(a.tile_order, a.tile, len(config["rays"]))
             if a.tile_order else None)
    result = render(config, tile=a.tile, workers=a.workers,
                    tile_order=order)
    a.output.write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n")
    print("pixels=%d spp=%d digest=%s mean_abs_bias=%.3e rms_sem=%.3e"
          % (result["stats"]["pixels"], result["stats"]["spp"],
             result["digest"], result["stats"]["mean_abs_xyz_bias"],
             result["stats"]["rms_standard_error"]))


if __name__ == "__main__":
    main()
