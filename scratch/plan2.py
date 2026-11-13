import sys, json
sys.path.insert(0, "scratch")
from deps import StreamModel

edl = json.load(open("demo.json"))

def build(name):
    m = StreamModel(edl["sources"][name])
    rows = m.rows
    n = len(rows)
    # first display frame pts is the EDL origin
    origin = min(r["pts"] for r in rows)
    # map decode index -> presentation order
    disp = m.order
    disp_pts = [rows[i]["pts"] for i in disp]
    # GOP boundaries in decode order: sync samples
    gops = []
    sync = [i for i in range(n) if rows[i]["sync"]]
    for k, s in enumerate(sync):
        e = sync[k+1] if k+1 < len(sync) else n
        gops.append((s, e))
    # open gop: does GOP contain frames presented before its first frame's pts?
    open_flags = []
    for s, e in gops:
        # frames in this gop whose pts < pts of s
        early = [j for j in range(s, e) if rows[j]["pts"] < rows[s]["pts"]]
        open_flags.append(bool(early))
    return m, origin, gops, open_flags

for name in ("video0", "video1", "video2"):
    m, origin, gops, open_flags = build(name)
    print("==", name, "origin", origin)
    for k, (s, e) in enumerate(gops):
        print(" gop", k, "decode", s, "..", e-1, "pts range", min(m.rows[j]["pts"] for j in range(s, e)),
              max(m.rows[j]["pts"] for j in range(s, e)), "open", open_flags[k],
              "idr", m.rows[s]["slice"]["idr"])
