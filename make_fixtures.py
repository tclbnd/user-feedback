#!/usr/bin/env python3
"""Generate acceptance fixtures for the speaker-label remap.

Every source channel carries an independent, globally unique pulse
(100*segment + channel index) so a routed output can be traced back to the
exact source channel/segment it came from -- no two channels ever share a
value, which makes swapped LFE/rear-surround routing unmissable.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

TARGET_51 = ["L", "R", "C", "LFE", "Ls", "Rs"]


def pulse(seg_idx, ch_idx, length):
    value = 100 * seg_idx + ch_idx
    return [value] * length


def segment(seg_idx, start, end, labels):
    n = end - start
    return {
        "start": start,
        "end": end,
        "source_speakers": list(labels),
        "samples": [pulse(seg_idx, c, n) for c in range(len(labels))],
    }


def build(target):
    # Segment boundaries are shared so layout switches happen on frame edges.
    # S0/S1: same 5.1 source delivered in two container track orders
    #        (the remux swaps the LFE slot with the rear-surround slots).
    # S2: partial layout (LFE missing) -> silence at the LFE target slot.
    # S3: decoder reports labels from a foreign layout -> unknown, reported.
    # S4: duplicated labels in the same frame -> conflict, muted.
    # S5: clean layout returns -> recovery after the conflict.
    layouts = [
        (0, 8, ["L", "R", "C", "LFE", "Ls", "Rs"]),       # standard order
        (8, 16, ["L", "R", "C", "Ls", "Rs", "LFE"]),       # remuxed order
        (16, 24, ["L", "R", "C", "Ls", "Rs"]),             # no LFE
        (24, 32, ["Aux1", "Aux2", "Aux3", "Aux4"]),        # unknown layout
        (32, 40, ["L", "R", "C", "LFE", "Ls", "Ls"]),      # duplicate Ls
        (40, 48, ["L", "R", "C", "LFE", "Ls", "Rs"]),      # recovery
    ]
    segments = [segment(i, s, e, labels) for i, (s, e, labels) in enumerate(layouts)]
    return {"target_speakers": list(target), "segments": segments}


def main():
    os.makedirs(os.path.join(HERE, "fixtures"), exist_ok=True)
    path = os.path.join(HERE, "fixtures", "remux51.json")
    with open(path, "w") as f:
        json.dump(build(TARGET_51), f, indent=2)
        f.write("\n")

    # Same-frame conflict micro-fixture: conflict shares its start frame with
    # the preceding segment's end frame (adjacent segments, one switch point).
    micro = {
        "target_speakers": TARGET_51,
        "segments": [
            segment(0, 0, 4, ["L", "R", "C", "LFE", "Ls", "Rs"]),
            segment(1, 4, 8, ["L", "L", "C", "LFE", "Ls", "Rs"]),
        ],
    }
    with open(os.path.join(HERE, "fixtures", "same_frame_conflict.json"), "w") as f:
        json.dump(micro, f, indent=2)
        f.write("\n")
    print("wrote", path)


if __name__ == "__main__":
    main()
