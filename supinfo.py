"""Summarise Blu-ray PGS subtitle streams (.sup) as JSON.

    python3 supinfo.py movie.sup [more files ...]
"""
import json
import struct
import sys

from supkit.sup import SupError, parse


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **parse(fh.read())})
        except (OSError, SupError, ValueError, IndexError, KeyError, struct.error) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
