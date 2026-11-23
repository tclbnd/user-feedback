"""Summarise SNES SPC music files as JSON.

    python3 spcinfo.py track.spc [more files ...]
"""
import json
import sys

from spckit.spc import SpcError, parse


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **parse(fh.read())})
        except (OSError, SpcError, ValueError, IndexError, KeyError) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
