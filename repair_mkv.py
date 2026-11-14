#!/usr/bin/env python3
"""Command line entry point: rebuild a truncated Matroska container.

Usage:
    python3 repair_mkv.py clipped.mkv restored.mkv report.json
"""

import argparse
import sys
from pathlib import Path

from mkvrecover.repair import RepairError, repair, write_report


def main():
    parser = argparse.ArgumentParser(
        description="Rebuild SeekHead/Cues and salvage blocks of a "
                    "truncated MKV/WebM file.")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()

    try:
        source_bytes = args.source.read_bytes()
    except OSError as exc:
        print("error: cannot read source: %s" % exc, file=sys.stderr)
        return 2

    try:
        output_bytes, report = repair(source_bytes)
    except RepairError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1

    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(output_bytes)
        write_report(report, args.report)
    except OSError as exc:
        print("error: cannot write output: %s" % exc, file=sys.stderr)
        return 2
    print("recovered blocks=%d discarded=%d cues=%d clusters=%d"
          % (len(report["recovered"]), len(report["discarded"]),
             report["cue_points"], report["clusters"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
