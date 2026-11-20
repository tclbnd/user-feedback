"""Duration and stream figures for Ogg Opus voice notes, as JSON.

    python3 opusinfo.py note1.opus note2.opus
"""
import json
import sys

from oggscan.ogg import OggError
from oggscan.probe import probe


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **probe(fh.read())})
        except (OggError, ValueError, OSError) as exc:
            out.append({"file": path, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
