#!/usr/bin/env python3
import json
import sys


def plan(track):
    frames = track["frames"]
    lengths = [frame["samples"] for frame in frames]
    total = sum(lengths)
    start, end = track["playable"]
    if start < 0 or end >= total:
        raise ValueError("playable interval outside decoded samples")

    # Starter defect: trims are applied only within their own frame.
    slices = []
    cursor = 0
    for index, frame in enumerate(frames):
        lo, hi = cursor, cursor + frame["samples"]
        trim_lo = min(frame["priming"], frame["samples"])
        trim_hi = max(trim_lo, frame["samples"] - frame["padding"])
        a, b = max(lo, start, lo + trim_lo), min(hi, end + 1, lo + trim_hi)
        if a < b:
            slices.append({"frame": index, "start": a - lo, "end": b - lo})
        cursor = hi
    return {"id": track["id"], "slices": slices, "samples": sum(x["end"]-x["start"] for x in slices)}


def main():
    source, target = sys.argv[1:3]
    with open(source, encoding="utf-8") as stream:
        payload = json.load(stream)
    result = {"sample_rate": payload["sample_rate"], "tracks": []}
    for track in payload["tracks"]:
        try:
            result["tracks"].append(plan(track))
        except (KeyError, TypeError, ValueError) as error:
            result["tracks"].append({"id": track.get("id"), "error": str(error)})
    with open(target, "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True)


if __name__ == "__main__":
    main()
