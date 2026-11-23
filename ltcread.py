"""Read linear timecode (LTC) from WAV recordings and print a JSON summary.

    python3 ltcread.py take01.wav [more files ...]
"""
import json
import struct
import sys

from ltckit.ltc import LtcError, decode


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **decode(fh.read())})
        except (OSError, LtcError, ValueError, IndexError, struct.error) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
