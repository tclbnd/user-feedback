#!/usr/bin/env python3
"""CLI: compare two page snapshots and emit a layered decision.

Usage
-----
    python3 diff.py old.json new.json output.json
    python3 diff.py --chunk 7 old.json new.json output.json

Output per comparison:

* ``class``        none / template / content / mixed
* ``refetch``      whether the content layer must be fetched again
* ``archive``      whether anything is archived, and which layers
* ``confidence``   overall decision confidence, plus per-layer confidences
* ``evidence``     layer digests, region location records, block diff,
                   relocation masking result, downgrade notes
* ``fingerprints`` every layer for both snapshots

Exit codes: 0 OK, 2 usage/IO error, 3 a snapshot could not be fingerprinted.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict

import engine


def load_page(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        raise engine.PageError("cannot read %s: %s" % (path, exc))
    except ValueError as exc:
        raise engine.PageError("cannot parse %s: %s" % (path, exc))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Layered structural/content fingerprint comparison.")
    parser.add_argument("old", help="old page snapshot JSON")
    parser.add_argument("new", help="new page snapshot JSON")
    parser.add_argument("output", help="output JSON path, or - for stdout")
    parser.add_argument("--chunk", type=int, default=0,
                        help="hash feed size; must not affect any digest")
    parser.add_argument("--pretty", action="store_true",
                        help="indent the output JSON")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = engine.compare(load_page(args.old), load_page(args.new),
                                chunk_size=args.chunk or None)
    except engine.PageError as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 3

    payload = json.dumps(result, ensure_ascii=False, sort_keys=True,
                         indent=2 if args.pretty else None)
    if args.output == "-":
        sys.stdout.write(payload + "\n")
    else:
        directory = os.path.dirname(os.path.abspath(args.output))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
