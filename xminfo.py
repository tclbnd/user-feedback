"""Summarise FastTracker II modules (.xm) as JSON.

    python3 xminfo.py song.xm [more files ...]
"""
import json
import struct
import sys

from xmkit.xm import XmError, parse


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **parse(fh.read())})
        except (OSError, XmError, IndexError, ValueError, struct.error) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
