import sys, json
sys.path.insert(0, "scratch")
from survey import analyze_video

edl = json.load(open("demo.json"))

def display_frames(rows):
    # display order = pts increasing
    order = sorted(range(len(rows)), key=lambda i: rows[i]["pts"])
    return order

for name in edl["sources"]:
    r = analyze_video(edl["sources"][name])
    order = display_frames(r["rows"])
    pts = [r["rows"][i]["pts"] for i in order]
    print("==", name)
    print(" display order:", order)
    print(" display pts:", pts)
    print(" first display pts:", pts[0])

print()
for ci, clip in enumerate(edl["clips"]):
    name = clip["source"]
    r = analyze_video(edl["sources"][name])
    rows = r["rows"]
    order = display_frames(rows)
    pts = [rows[i]["pts"] for i in order]
    start, end = clip["start_ms"], clip["end_ms"]
    # source first display frame is pts[0] = 100
    rel = [p - pts[0] for p in pts]
    sel = [k for k, v in enumerate(rel) if start <= v < end]
    print("clip", ci, name, start, end, "rel window", (start - pts[0], end - pts[0]),
          "selected display idx", sel[0], "..", sel[-1], "count", len(sel),
          "first frame row", order[sel[0]], "pts", pts[sel[0]],
          "last frame row", order[sel[-1]], "pts", pts[sel[-1]])
