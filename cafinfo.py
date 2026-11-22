"""Summarise Core Audio Format files (.caf) as JSON.

    python3 cafinfo.py memo.caf [more files ...]
"""
import json
import struct
import sys

from cafkit.caf import CafError, parse


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **parse(fh.read())})
        except (OSError, CafError, IndexError, ValueError, struct.error) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
