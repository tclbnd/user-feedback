"""Summarise MP4 / MOV sample tables as JSON.

    python3 mp4info.py clip.mp4 [more files ...]
"""
import json
import struct
import sys

from mp4kit.boxes import Mp4Error
from mp4kit.tracks import movie


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **movie(fh.read())})
        except (Mp4Error, OSError, struct.error, TypeError, IndexError, ZeroDivisionError) as exc:
            out.append({"file": path, "error": str(exc) or type(exc).__name__})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
