"""Print the catalogue summary of FLAC files as JSON.

    python3 flacinfo.py masters/*.flac
"""
import json
import sys

from flacmeta.blocks import FlacError
from flacmeta.probe import probe


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **probe(fh.read())})
        except (FlacError, OSError) as exc:
            out.append({"file": path, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
