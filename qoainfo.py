"""Summarise QOA (Quite OK Audio) files as JSON.

    python3 qoainfo.py sfx.qoa [more files ...]
"""
import json
import struct
import sys

from qoakit.qoa import QoaError, parse


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **parse(fh.read())})
        except (OSError, QoaError, ValueError, IndexError, struct.error) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
