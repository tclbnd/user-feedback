#!/usr/bin/env python3
"""Speaker label remap.

The container/decoder reports an *explicit* ordered speaker id per source
channel for every segment.  Positions are resolved strictly by speaker-id
identity against the playback layout (target_speakers); ids are never sorted
or otherwise guessed (the classic remux failure is the LFE slot carrying the
rear-surround label, which an alphabetical/positional guess swaps silently).

Per segment:
  * duplicate source speaker ids  -> "conflict", whole segment muted;
  * no source id matches a target -> "unknown", whole segment muted and the
    layout is reported rather than guessed;
  * otherwise "ok": each target position takes the matching source channel,
    and any target id the source layout does not carry is silence-filled at
    the *target* position (map entry -1, reason recorded in "missing").
"""
import json
import sys


def remap(data):
    target = list(data["target_speakers"])
    nch = len(target)
    channels = [[] for _ in range(nch)]
    report = []

    for seg in data["segments"]:
        src = list(seg["source_speakers"])
        samples = seg["samples"]
        n = seg["end"] - seg["start"]

        # First occurrence index + duplicated-id detection, all keyed on the
        # explicit speaker labels reported for this segment.
        first_idx = {}
        duplicates = []
        for i, sid in enumerate(src):
            if sid in first_idx:
                if sid not in duplicates:
                    duplicates.append(sid)
            else:
                first_idx[sid] = i

        # Resolve every output position by speaker-id identity only.
        mapping = [-1] * nch
        missing = []
        if duplicates:
            # Ambiguous track layout: which physical channel a duplicated
            # label points at cannot be decided, so nothing is routed.
            status = "conflict"
        else:
            for p, tid in enumerate(target):
                if tid in first_idx:
                    mapping[p] = first_idx[tid]
                else:
                    missing.append(tid)
            status = "unknown" if not any(m >= 0 for m in mapping) else "ok"

        report.append({
            "start": seg["start"],
            "end": seg["end"],
            "status": status,
            "map": list(mapping),
            "missing": missing,
            "duplicates": duplicates,
            "source_speakers": src,
        })

        # Render the segment into target-position order. Conflict/unknown
        # segments and unmatched target positions are silence filled.
        muted = status in ("conflict", "unknown")
        for p in range(nch):
            if muted:
                channels[p].extend([0] * n)
                continue
            s = mapping[p]
            if s < 0:
                channels[p].extend([0] * n)  # gap filled at target position
            else:
                vals = list(samples[s])[:n]
                if len(vals) < n:  # tolerant of short decoded blocks
                    vals += [0] * (n - len(vals))
                channels[p].extend(vals)

    return {"channels": channels, "segment_report": report}


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: python3 remap.py input.json output.json")
    with open(sys.argv[1]) as f:
        data = json.load(f)
    result = remap(data)
    with open(sys.argv[2], "w") as f:
        json.dump(result, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
