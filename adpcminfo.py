"""Summarise ADPCM WAVE files as JSON.

    python3 adpcminfo.py clip.wav [more files ...]
"""
import json
import sys

from adpcmkit.info import summary
from adpcmkit.wav import WavError


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **summary(fh.read())})
        except (WavError, OSError, ValueError, IndexError, struct_error()) as exc:
            out.append({"file": path, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


def struct_error():
    import struct
    return struct.error


if __name__ == "__main__":
    main(sys.argv[1:])
