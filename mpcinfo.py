"""Summarise Musepack SV8 files (.mpc) as JSON.

    python3 mpcinfo.py album.mpc [more files ...]
"""
import json
import struct
import sys

from mpckit.mpc import MpcError, parse


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **parse(fh.read())})
        except (OSError, MpcError, ValueError, IndexError, struct.error) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
