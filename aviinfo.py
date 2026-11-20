"""Print stream and index summary of AVI files as JSON.

    python3 aviinfo.py cam01.avi ...
"""
import json
import mmap
import sys

from aviscan.index import scan
from aviscan.riff import AviError


def read(path):
    """Map the file instead of reading it: OpenDML files often exceed 1 GB."""
    with open(path, "rb") as fh:
        try:
            with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                return scan(mm)
        except ValueError:                 # empty file: mmap refuses
            return scan(fh.read())


def main(paths):
    out = []
    for path in paths:
        try:
            out.append({"file": path, "streams": read(path)})
        except (AviError, OSError, AttributeError, ValueError) as exc:
            out.append({"file": path, "error": str(exc) or type(exc).__name__})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
