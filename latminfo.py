"""Summarise LOAS/LATM AAC streams as JSON.

    python3 latminfo.py capture.latm [more files ...]
"""
import json
import sys

from latmkit.latm import LatmError, scan


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **scan(fh.read())})
        except (LatmError, OSError, EOFError, IndexError) as exc:
            out.append({"file": path, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
