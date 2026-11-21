"""Summarise Bluetooth SBC / mSBC captures as JSON.

    python3 sbcinfo.py capture.sbc [more files ...]

Files ending in .a2dp are dumps of A2DP media payloads (see README).
"""
import json
import sys

from sbckit.header import FrameError
from sbckit.stream import summary


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **summary(fh.read(), a2dp=path.endswith(".a2dp"))})
        except (FrameError, OSError, IndexError) as exc:
            out.append({"file": path, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
