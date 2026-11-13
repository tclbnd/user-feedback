import sys, json
sys.path.insert(0, "scratch")
import survey
from deps import StreamModel

edl = json.load(open("demo.json"))

for name in ("video0", "video1", "video2"):
    m = StreamModel(edl["sources"][name])
    rows = m.rows
    print("==", name, "n", len(rows))
    # identify sync samples and check open GOP: a sync sample whose following samples (in decode order)
    # have pts less than sync sample's pts (i.e. B frames presented before the I frame)
    sync = [i for i, row in enumerate(rows) if row["sync"]]
    print("sync decode indices:", sync)
    for s in sync:
        after = []
        for j in range(s+1, min(s+6, len(rows))):
            after.append((j, rows[j]["pts"], rows[j]["slice"]["type"]))
        before = [(j, rows[j]["pts"]) for j in range(s-4, s) if j >= 0]
        print(" sync", s, "pts", rows[s]["pts"], "type", rows[s]["slice"]["type"],
              "idr", rows[s]["slice"]["idr"], "after", after)
