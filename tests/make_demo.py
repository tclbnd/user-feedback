"""Generate a multi-shot VFR demo sequence and CLI-level rerun checks.

Usage:
    python3 tests/make_demo.py            # writes tests/demo_input.json
"""
import json
import random
from pathlib import Path

SEED = 20261113


def frame(rng, w, h, siting, range_kind, burst=False):
    cw, ch = (w + 1) // 2, (h + 1) // 2
    if burst:
        y = [[rng.randint(880, 940) for _ in range(w)] for _ in range(h)]
    else:
        y = [[rng.randint(64, 700) for _ in range(w)] for _ in range(h)]
    return {
        "width": w,
        "height": h,
        "metadata": {
            "primaries": "bt2020", "transfer": "pq", "matrix": "bt2020nc",
            "range": range_kind, "siting": siting,
        },
        "y": y,
        "cb": [[rng.randint(128, 896) for _ in range(cw)] for _ in range(ch)],
        "cr": [[rng.randint(128, 896) for _ in range(cw)] for _ in range(ch)],
    }


def main():
    rng = random.Random(SEED)
    frames = []
    pts = 0
    # (w, h, siting, range, duration, cut, burst)
    spec = [
        (5, 3, "center", "limited", 3, True, False),
        (5, 3, "left", "limited", 7, False, False),
        (5, 3, "left", "full", 2, False, True),    # short highlight
        (5, 3, "center", "limited", 4, False, False),
        (4, 6, "left", "limited", 5, True, False),  # cut + size change
        (4, 6, "center", "full", 2, False, True),
        (4, 6, "left", "limited", 6, False, False),
        (7, 7, "center", "limited", 9, True, False),
    ]
    for w, h, siting, range_kind, dur, cut, burst in spec:
        entry = {"pts": pts, "duration": dur}
        if cut:
            entry["cut"] = True
        entry["frame"] = frame(rng, w, h, siting, range_kind, burst)
        frames.append(entry)
        pts += dur
    payload = {
        "time_base": [1, 90000],
        "target_siting": "center",
        "frames": frames,
    }
    out = Path(__file__).resolve().parent / "demo_input.json"
    out.write_text(json.dumps(payload))
    print("wrote", out, "frames:", len(frames), "end pts:", pts)


if __name__ == "__main__":
    main()
