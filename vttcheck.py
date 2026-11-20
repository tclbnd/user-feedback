"""Parse WebVTT files.

    python3 vttcheck.py captions.vtt            cue list as JSON
    python3 vttcheck.py --srt captions.vtt      SubRip export
"""
import json
import sys

from vttkit import srt
from vttkit.parser import VttError, parse


def main(args):
    to_srt = args and args[0] == "--srt"
    path = args[1] if to_srt else args[0]
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    try:
        cues = parse(text)
    except VttError as exc:
        print(json.dumps({"file": path, "error": str(exc)}, ensure_ascii=False))
        return
    if to_srt:
        sys.stdout.write(srt.export(cues))
    else:
        print(json.dumps({"file": path, "cues": cues}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
