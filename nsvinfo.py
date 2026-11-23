"""Summarise Nullsoft Streaming Video files as JSON.

    python3 nsvinfo.py show.nsv [more files ...]
"""
import json
import struct
import sys

from nsvkit.nsv import NsvError, parse


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **parse(fh.read())})
        except (OSError, NsvError, ValueError, IndexError, KeyError, struct.error, ZeroDivisionError) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
