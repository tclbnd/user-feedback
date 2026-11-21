"""Summarise IFF 8SVX instruments as JSON.

    python3 svxinfo.py bass.8svx [more files ...]
"""
import json
import struct
import sys

from svxkit.svx import SvxError, summary


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **summary(fh.read())})
        except (SvxError, OSError, IndexError, struct.error) as exc:
            out.append({"file": path, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
