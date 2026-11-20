"""Summarise AC-3 / E-AC-3 elementary streams as JSON.

    python3 dolbyinfo.py programme.ac3 ...
"""
import json
import sys

from dolbyscan.stream import scan
from dolbyscan.syncframe import SyncError


def main(paths):
    out = []
    for path in paths:
        try:
            out.append({"file": path, **scan(open(path, "rb").read())})
        except (SyncError, OSError, IndexError) as exc:
            out.append({"file": path, "error": str(exc) or type(exc).__name__})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
