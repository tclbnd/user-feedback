"""Summarise ASF (Windows Media) file headers as JSON.

    python3 asfinfo.py song.wma [more files ...]
"""
import json
import struct
import sys

from asfkit.asf import AsfError, parse


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **parse(fh.read())})
        except (OSError, AsfError, ValueError, IndexError, struct.error) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
