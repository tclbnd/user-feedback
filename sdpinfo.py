"""Summarise SDP session descriptions (AES67 / ST 2110 streams) as JSON.

    python3 sdpinfo.py stream.sdp [more files ...]
"""
import json
import sys

from sdpkit.sdp import SdpError, parse


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                out.append({"file": path, **parse(fh.read())})
        except (OSError, SdpError, ValueError, IndexError, KeyError, ZeroDivisionError) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
