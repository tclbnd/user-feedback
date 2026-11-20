"""Summarise AIFF and AIFF-C files as JSON.

    python3 aiffinfo.py take01.aif [more files ...]
"""
import json
import sys

from aifftool.chunks import AiffError
from aifftool.info import summary


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **summary(fh.read())})
        except (AiffError, OSError, ValueError) as exc:
            out.append({"file": path, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
