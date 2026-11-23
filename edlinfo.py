"""Summarise CMX 3600 edit decision lists as JSON.

    python3 edlinfo.py [--fps 24|25|30] cut.edl [more files ...]
"""
import argparse
import json
import sys

from edlkit.edl import EdlError, parse


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fps", type=int, default=30, choices=(24, 25, 30))
    ap.add_argument("paths", nargs="+")
    args = ap.parse_args(argv)
    out = []
    for path in args.paths:
        try:
            with open(path, encoding="latin-1") as fh:
                out.append({"file": path, **parse(fh.read(), args.fps)})
        except (OSError, EdlError, ValueError, IndexError, KeyError) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
