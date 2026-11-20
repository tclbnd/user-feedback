"""Tempo map, notes and lyric times of a MIDI file as JSON.

    python3 midiinfo.py song.mid
"""
import json
import sys

from midiclock.smf import MidiError
from midiclock.timing import analyse


def main(path):
    with open(path, "rb") as fh:
        data = fh.read()
    try:
        print(json.dumps({"file": path, **analyse(data)}, ensure_ascii=False, indent=1))
    except (MidiError, IndexError) as exc:
        print(json.dumps({"file": path, "error": str(exc) or type(exc).__name__}))


if __name__ == "__main__":
    main(sys.argv[1])
