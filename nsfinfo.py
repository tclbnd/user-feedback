"""Summarise NSF and NSFe music files as JSON.

    python3 nsfinfo.py game.nsf [more files ...]
"""
import json
import sys

from nsfkit.nsf import NsfError, parse


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **parse(fh.read())})
        except (OSError, NsfError, ValueError, IndexError, KeyError) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
