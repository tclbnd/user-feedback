"""Summarise Core Audio Format (.caf) files as JSON.

    python3 cafinfo.py take.caf [more files ...]
"""
import json
import sys

from caftool.chunks import CafError
from caftool.info import summary


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **summary(fh.read())})
        except (CafError, OSError, ValueError) as exc:
            out.append({"file": path, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
