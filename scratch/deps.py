import sys
sys.path.insert(0, "scratch")
import survey
import h264

class StreamModel:
    def __init__(self, path):
        r = survey.analyze_video(path)
        self.r = r
        self.rows = r["rows"]
        self.sps = r["sps"]
        self.pps = r["pps"]
        self.length_size = r["length_size"]
        self.data = r["data"]
        self.st = r["st"]
        n = len(self.rows)
        self.order = sorted(range(n), key=lambda i: (self.rows[i]["pts"], i))
        self.display_index = [None] * n
        for k, i in enumerate(self.order):
            self.display_index[i] = k
        self.poc = [row["slice"]["poc"] for row in self.rows]

    def decode_order_refs(self):
        # H.264 sliding window DPB simulation (no MMCO in these streams - verify)
        maxref = self.sps["max_num_ref_frames"]
        dpb = []  # list of sample indices in arrival order
        refs = {}
        seen_poc = []
        for i, row in enumerate(self.rows):
            sl = row["slice"]
            is_ref = sl["nri"] != 0
            if sl["type"] in ("I", "P", "B"):
                if sl["type"] == "P":
                    refs[i] = list(dpb)[-1:]
                elif sl["type"] == "B":
                    refs[i] = list(dpb)
                else:
                    refs[i] = []
            if sl["idr"] is not None:
                dpb = []
            if is_ref:
                dpb.append(i)
                maxdpb = maxref
                while len(dpb) > maxdpb:
                    dpb.pop(0)
        return refs

if __name__ == "__main__":
    for p in ("video0.mp4", "video1.mp4", "video2.mp4"):
        m = StreamModel(p)
        refs = m.decode_order_refs()
        print("==", p)
        print("mmco check:", [row["slice"].get("adaptive_marking") for row in m.rows])
        for i in range(len(m.rows)):
            print("%2d disp=%-2d %s pts=%-5d refs=%s" % (
                i, m.display_index[i], m.rows[i]["slice"]["type"], m.rows[i]["pts"], refs.get(i)))
