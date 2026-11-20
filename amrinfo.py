"""Summarise AMR voicemail files as JSON.

    python3 amrinfo.py msg0001.amr ...
"""
import json
import sys

from amrfile.frames import AmrError
from amrfile.summary import summarise


def main(paths):
    out = []
    for path in paths:
        try:
            out.append({"file": path, **summarise(open(path, "rb").read())})
        except (AmrError, OSError, IndexError) as exc:
            out.append({"file": path, "error": str(exc) or type(exc).__name__})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
