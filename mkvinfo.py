"""Summarise Matroska / WebM files as JSON.

    python3 mkvinfo.py clip.webm [more files ...]
"""
import json
import struct
import sys

from mkvkit.ebml import EbmlError
from mkvkit.mkv import scan


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **scan(fh.read())})
        except (EbmlError, OSError, struct.error, IndexError, UnicodeDecodeError) as exc:
            out.append({"file": path, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
