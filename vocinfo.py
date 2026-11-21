"""Summarise Creative Voice Files (.voc) as JSON.

    python3 vocinfo.py speech.voc [more files ...]
"""
import json
import struct
import sys

from vockit.voc import VocError, scan


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **scan(fh.read())})
        except (VocError, OSError, IndexError, struct.error) as exc:
            out.append({"file": path, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
