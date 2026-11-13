"""Offline frame conversion front end with an existing YUV plane reader."""
import argparse
import json
from pathlib import Path


def read_frame(path):
    frame = json.loads(path.read_text())
    w, h = frame["width"], frame["height"]
    if type(w) is not int or type(h) is not int or w < 1 or h < 1:
        raise ValueError("invalid frame dimensions")
    for plane, pw, ph in [("y", w, h), ("cb", (w + 1)//2, (h + 1)//2),
                           ("cr", (w + 1)//2, (h + 1)//2)]:
        rows = frame[plane]
        if len(rows) != ph or any(len(row) != pw for row in rows):
            raise ValueError("invalid " + plane + " plane shape")
        if any(type(x) is not int or not 0 <= x <= 1023 for row in rows for x in row):
            raise ValueError("invalid " + plane + " sample")
    return frame


def convert(frame, seed=None, debug=False):
    # Old converter treats PQ as linear and merely rescales all three planes.
    out = {
        "width": frame["width"],
        "height": frame["height"],
        "metadata": dict(frame.get("metadata", {})),
    }
    for plane in ("y", "cb", "cr"):
        out[plane] = [[v >> 2 for v in row] for row in frame[plane]]
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    try:
        result = convert(read_frame(args.input), args.seed, args.debug)
        encoded = json.dumps(result, allow_nan=False)
    except (ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    args.output.write_text(encoded + "\n")


if __name__ == "__main__":
    main()
