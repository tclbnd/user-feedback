"""Summarise APE tags as JSON.

    python3 apeinfo.py track.ape [more files ...]
"""
import json
import struct
import sys

from apekit.ape import ApeError, parse


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **parse(fh.read())})
        except (ApeError, OSError, IndexError, struct.error, ValueError) as exc:
            out.append({"file": path, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
