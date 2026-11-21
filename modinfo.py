"""Summarise tracker modules (.mod) as JSON.

    python3 modinfo.py tune.mod [more files ...]
"""
import json
import sys

from modkit.info import summary
from modkit.module import ModError


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **summary(fh.read())})
        except (ModError, OSError, IndexError, ValueError) as exc:
            out.append({"file": path, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
