"""Summarise AV1 streams in IVF files as JSON.

    python3 av1info.py clip.ivf [more files ...]
"""
import json
import sys

from av1kit.av1 import Av1Error, scan


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **scan(fh.read())})
        except (OSError, Av1Error, IndexError, ValueError) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
