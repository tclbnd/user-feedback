"""Print ID3v2 tag summaries as JSON.

    python3 id3dump.py episode.mp3 ...
"""
import json
import sys

from tagread.id3 import TagError
from tagread.summary import summarise


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **summarise(fh.read())})
        except (TagError, OSError) as exc:
            out.append({"file": path, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
