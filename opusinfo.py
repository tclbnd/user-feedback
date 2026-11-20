"""Duration and stream figures for Ogg Opus voice notes, as JSON.

    python3 opusinfo.py note1.opus note2.opus

Exit status is 0 when every file was parsed (even if it had integrity
warnings) and 1 when at least one file could not be read as Ogg Opus.
"""
import json
import sys

from oggscan.ogg import OggError
from oggscan.probe import probe


def main(paths):
    out = []
    failed = False
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **probe(fh.read())})
        except (OggError, ValueError, OSError) as exc:
            failed = True
            out.append({"file": path, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
