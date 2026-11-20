"""Summarise IVF/AV1 clips as JSON.

    python3 ivfinfo.py clip.ivf ...
"""
import json
import sys

from ivfscan.ivf import IvfError
from ivfscan.summary import summarise


def main(paths):
    out = []
    for path in paths:
        try:
            out.append({"file": path, **summarise(open(path, "rb").read())})
        except (IvfError, OSError, IndexError) as exc:
            out.append({"file": path, "error": str(exc) or type(exc).__name__})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
