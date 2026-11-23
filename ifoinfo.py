"""Summarise DVD-Video title set IFO files as JSON.

    python3 ifoinfo.py VTS_01_0.IFO [more files ...]
"""
import json
import struct
import sys

from ifokit.ifo import IfoError, parse


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **parse(fh.read())})
        except (OSError, IfoError, ValueError, IndexError, KeyError, struct.error) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
