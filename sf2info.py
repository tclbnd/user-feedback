"""Summarise SoundFont 2 banks (.sf2) as JSON.

    python3 sf2info.py bank.sf2 [more files ...]
"""
import json
import struct
import sys

from sf2kit.info import summary
from sf2kit.riff import Sf2Error


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **summary(fh.read())})
        except (Sf2Error, OSError, IndexError, struct.error, KeyError) as exc:
            out.append({"file": path, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
