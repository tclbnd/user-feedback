"""Decode SCTE 35 cue messages, one per line (base64 or 0x-prefixed hex).

    python3 scteinfo.py cues.txt [more files ...]
"""
import binascii
import json
import sys

from scte.splice import SpliceError, decode_line, parse


def main(paths):
    out = []
    for path in paths:
        with open(path, encoding="ascii") as fh:
            for n, line in enumerate(fh, 1):
                if not line.strip() or line.startswith("#"):
                    continue
                try:
                    out.append({"file": path, "line": n, **parse(decode_line(line))})
                except (SpliceError, EOFError, binascii.Error, ValueError) as exc:
                    out.append({"file": path, "line": n, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
