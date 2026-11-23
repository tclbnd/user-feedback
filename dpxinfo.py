"""Summarise DPX film scan frames as JSON.

    python3 dpxinfo.py frame_000100.dpx [more files ...]
"""
import json
import struct
import sys

from dpxkit.dpx import DpxError, parse


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **parse(fh.read())})
        except (OSError, DpxError, ValueError, IndexError, KeyError, struct.error) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
