#!/usr/bin/env python3
"""Timeline rebuild from out-of-order, possibly overlapping fragments.

Contract (see README.md):
  * samples are sorted by decode time (bmdt + cumulative sample durations
    inside a fragment), never by arrival order;
  * a sample is keyed by its decode-time start; on collision the fragment
    with a higher `seq` wins, and without `seq` the later arrival wins;
  * decode-time holes become gap entries (end-of-stream holes do not);
  * probes inside a gap or past the end report the next sample or null.
"""
import json
import sys


def _iter_fragment_samples(fr, arrival):
    dts = fr["bmdt"]
    for idx, s in enumerate(fr["samples"]):
        yield {
            "pts": dts,
            "dur": s["duration"],
            "size": s["size"],
            "_rank": (fr["seq"], arrival) if "seq" in fr else None,
            "_arrival": arrival,
            "_order": idx,
        }
        dts += s["duration"]


def _wins(candidate, current):
    if current is None:
        return True
    if candidate["_rank"] is not None and current["_rank"] is not None:
        return candidate["_rank"] > current["_rank"]
    # no seq on either side: later arrival wins
    return candidate["_arrival"] > current["_arrival"]


def build_timeline(fragments):
    """Collapse overlapping fragments into one decode-time sorted map."""
    chosen = {}
    for arrival, fr in enumerate(fragments):
        for sample in _iter_fragment_samples(fr, arrival):
            key = sample["pts"]
            if _wins(sample, chosen.get(key)):
                chosen[key] = sample
    return [chosen[k] for k in sorted(chosen)]


def build_gaps(samples):
    """Decode-time holes strictly between two samples."""
    gaps = []
    for prev, cur in zip(samples, samples[1:]):
        hole_start = prev["pts"] + prev["dur"]
        if hole_start < cur["pts"]:
            gaps.append({"pts": hole_start, "dur": cur["pts"] - hole_start})
    return gaps


def probe(samples, gaps, tt):
    for s in samples:
        if s["pts"] <= tt < s["pts"] + s["dur"]:
            return True, s["pts"], s["dur"], s["size"]
    # inside a gap, before the first sample, or past the last one
    nearest = next((s["pts"] for s in samples if s["pts"] > tt), None)
    return False, nearest, None, None


def _public(sample):
    return {"pts": sample["pts"], "dur": sample["dur"], "size": sample["size"]}


def main():
    with open(sys.argv[1]) as f:
        d = json.load(f)
    out_tracks = []
    track_map = {}
    for t in d["tracks"]:
        merged = build_timeline(t["fragments"])
        gaps = build_gaps(merged)
        timeline = [_public(s) for s in merged]
        if merged:
            duration_ms = (merged[-1]["pts"] + merged[-1]["dur"]) / t["timescale"]
        else:
            duration_ms = 0
        out_tracks.append({
            "id": t["id"],
            "timeline": timeline,
            "gaps": gaps,
            "duration_ms": duration_ms,
        })
        track_map[t["id"]] = (t, merged, gaps)

    answers = []
    for q in d["targets"]:
        t, samples, gaps = track_map[q["track"]]
        tt = int(q["t_ms"] * t["timescale"] / 1000)
        found, pts, dur, size = probe(samples, gaps, tt)
        answers.append({
            "track": q["track"],
            "t_ms": q["t_ms"],
            "found": found,
            "pts": pts,
            "dur": dur,
            "size": size,
        })
    with open(sys.argv[2], "w") as f:
        json.dump({"tracks": out_tracks, "answers": answers}, f, indent=2)


if __name__ == "__main__":
    main()
