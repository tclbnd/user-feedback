#!/usr/bin/env python3
"""HDR zone metering and edge-aware local tone mapping.

    python3 tm.py req.json output.json

req.json is a JSON object: {"hdr": ..., "tile": 16, "zones": [4, 4], ...}
where "hdr" is either an inline array or a path (relative to req.json) to a
JSON file holding one.  A 2-D array is luminance; a 3-D array is RGB(A) and is
tone mapped on Rec.709 luma with the colour ratios restored afterwards.

See README.md for the full request/response contract and hdr_tm/ for the
implementation (standard library only; numpy / scipy are not required).
"""
import sys

from hdr_tm.pipeline import run_file

USAGE = "usage: python3 tm.py req.json output.json"


def main(argv):
    if len(argv) != 2:
        sys.stderr.write(USAGE + "\n")
        return 2
    try:
        run_file(argv[0], argv[1])
    except (ValueError, OSError) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
