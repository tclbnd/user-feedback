#!/usr/bin/env python3
import json, sys

def main():
    d = json.load(open(sys.argv[1]))
    fr = d["frames"]; tag = d["tag_bytes"]; tgt = d["target"]
    n = len(fr)

    # Byte offsets: leading tag + sum of ALL prior frame lens
    # (corrupt frames still occupy bytes in the file).
    offs = []; o = tag
    for f in fr:
        offs.append(o); o += f["len"]

    # Cumulative VALID decoded samples (corrupt frames produce none).
    cum = []; c = 0
    for f in fr:
        if f["valid"]:
            c += f["samples"]
        cum.append(c)

    # Locate target frame: first valid frame whose cumulative
    # valid-sample count passes tgt (exact, no average-bitrate guess).
    tf = -1
    for i, f in enumerate(fr):
        if f["valid"] and cum[i] > tgt:
            tf = i
            break
    eof = tf < 0
    if eof:
        # target past last valid sample -> plan for the final valid frame
        for i in range(n - 1, -1, -1):
            if fr[i]["valid"]:
                tf = i
                break
        if tf < 0:
            tf = n - 1  # degenerate: no valid frames at all

    # Reservoir walk-back. A corrupt frame supplies no reservoir bytes
    # and breaks the chain: the plan may not start at or before it.
    min_p = 0
    for i in range(tf, -1, -1):
        if not fr[i]["valid"]:
            min_p = i + 1
            break
    min_p = min(min_p, tf)

    def covered(p):
        # each frame j in [p..tf] needs main_begin[j] bytes supplied
        # by valid frames in [p..j)
        b = 0
        for j in range(p, tf + 1):
            if b < fr[j]["main_begin"]:
                return False
            if fr[j]["valid"]:
                b += fr[j]["len"]
        return True

    # Walk back only as far as needed, never past the corrupt barrier.
    p = tf
    while p > min_p and not covered(p):
        p -= 1
    ok = covered(p)

    notes = ["reservoir-ok" if ok else "reservoir-broken"]
    if eof:
        notes.append("target-eof")

    # discard: valid samples to drop from the start of decoding so
    # output begins at tgt (frames before p are not decoded at all).
    prev_valid = cum[p] - (fr[p]["samples"] if fr[p]["valid"] else 0)
    discard = max(0, tgt - prev_valid)

    json.dump({"start_frame": p,
               "byte_offset": offs[p] if 0 <= p < n else tag,
               "discard": discard,
               "reliable": ok,
               "notes": notes},
              open(sys.argv[2], "w"), indent=1)

main()
