"""Summarise Sony Wave64 files (.w64) as JSON.

    python3 w64info.py take.w64 [more files ...]
"""
import json
import struct
import sys

from w64kit.w64 import W64Error, summary


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **summary(fh.read())})
        except (W64Error, OSError, IndexError, struct.error) as exc:
            out.append({"file": path, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
