"""Print stream and index summary of AVI files as JSON.

    python3 aviinfo.py cam01.avi ...
"""
import json
import sys

from aviscan.index import scan
from aviscan.riff import AviError


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, "streams": scan(fh.read())})
        except (AviError, OSError, AttributeError, ValueError) as exc:
            out.append({"file": path, "error": str(exc) or type(exc).__name__})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
