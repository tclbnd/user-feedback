"""Measure programme loudness of WAV files (ITU-R BS.1770 / EBU R 128) as JSON.

    python3 lufsinfo.py mix.wav [more files ...]
"""
import json
import struct
import sys

from lufskit.lufs import LufsError, parse


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **parse(fh.read())})
        except (OSError, LufsError, ValueError, IndexError, KeyError, struct.error) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
