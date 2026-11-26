"""Summarise each file as JSON.

    python3 cfinfo.py FILE [more files ...]
"""
import json
import struct
import sys

from chanf.vram import ChanFError, parse


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            out.append({"file": path, **parse(data)})
        except (OSError, ChanFError, ValueError, IndexError, KeyError, ZeroDivisionError, struct.error, UnicodeDecodeError) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
