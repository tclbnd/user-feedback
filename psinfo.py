"""Summarise MPEG program streams (.mpg, .vob) as JSON.

    python3 psinfo.py title.vob [more files ...]
"""
import json
import sys

from psscan.headers import PsError
from psscan.scan import scan


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **scan(fh.read())})
        except (PsError, OSError, IndexError) as exc:
            out.append({"file": path, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
