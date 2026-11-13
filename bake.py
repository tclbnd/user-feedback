"""Command line entry point for the chart-isolated lightmap baker.

Usage: python3 bake.py demo.json output.json [--no-trace]

The output schema keeps the prototype contract (charts[id].levels with
size/light/owners, plus a mask tag) and adds the assembled atlas levels
with per-level conflict records and a trace section describing the
owning chart / triangle / barycentric position / normal of every texel.
"""
import argparse
import json
from pathlib import Path

from lightmap import bake


def main():
    p = argparse.ArgumentParser()
    p.add_argument("input", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--no-trace", action="store_true",
                   help="omit per-texel trace records from the JSON output")
    a = p.parse_args()
    result = bake(json.loads(a.input.read_text()), trace=not a.no_trace)
    a.output.write_text(json.dumps(result, sort_keys=True) + "\n")
    print("baked {0} charts, {1} atlas level(s) -> {2}".format(
        len(result["charts"]), len(result["atlas"]), a.output))


if __name__ == "__main__":
    main()
