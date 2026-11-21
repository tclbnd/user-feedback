"""List markers, regions and loops of WAV files as JSON.

    python3 wavmarks.py take.wav [more files ...]
"""
import json
import struct
import sys

from wavkit.riff import WavError, parse


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **parse(fh.read())})
        except (WavError, OSError, struct.error) as exc:
            out.append({"file": path, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
