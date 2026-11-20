"""Summarise FLV recordings as JSON.

    python3 flvinfo.py live_0412.flv ...
"""
import json
import sys

from flvscan.amf import AmfError
from flvscan.summary import summarise
from flvscan.tags import FlvError


def main(paths):
    out = []
    for path in paths:
        try:
            out.append({"file": path, **summarise(open(path, "rb").read())})
        except (FlvError, AmfError, OSError, IndexError) as exc:
            out.append({"file": path, "error": str(exc) or type(exc).__name__})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
