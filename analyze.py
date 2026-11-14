import json
from fractions import Fraction
from inspect_mp4 import track_infos, samples_from_info, find, u32

def load(path):
    data, infos = track_infos(path)
    v = [i for i in infos if i.get("handler") == "vide"][0]
    a = [i for i in infos if i.get("handler") == "soun"][0]
    media_time = v["elst"][0][1] if v["elst"] else 0
    samples = samples_from_info(data, v)
    frames = []
    for s in samples:
        disp = s["pts"] - media_time
        frames.append(dict(idx=s["i"], pts=s["pts"], disp=disp, key=s["key"], dur=s["duration"]))
    display = sorted(frames, key=lambda f: f["disp"])
    # gops
    keys = [f["idx"] for f in display if f["key"]]
    keys_sorted = sorted(keys)
    gops = []
    for kpos, k in enumerate(keys_sorted):
        end = keys_sorted[kpos + 1] if kpos + 1 < len(keys_sorted) else len(samples)
        ids = list(range(k, end))
        ds = sorted(ids, key=lambda i: samples[i]["pts"])
        gops.append(dict(start=k, end=end, ids=ids, display_ids=ds,
                         disp_min=samples[ds[0]]["pts"] - media_time,
                         disp_max=samples[ds[-1]]["pts"] - media_time,
                         is_idr=None))
    return dict(data=data, info=v, samples=samples, frames=frames, display=display, gops=gops,
                media_time=media_time, ts=v["timescale"], audio=a)

clips = json.load(open("demo.json"))["clips"]
srcs = {}
for name, path in json.load(open("demo.json"))["sources"].items():
    srcs[name] = load(path)

N = sum(c["end_ms"] - c["start_ms"] for c in clips)
print("total ms", N)
cum = 0
for ci, c in enumerate(clips):
    s = srcs[c["source"]]
    disp = [f for f in s["display"] if c["start_ms"] <= f["disp"] < c["end_ms"]]
    print("clip", ci, c, "cum", cum, "n", len(disp), "disp", [f["disp"] for f in disp])
    # gop containment
    gsel = []
    for gi, g in enumerate(s["gops"]):
        allsel = all(c["start_ms"] <= s["samples"][i]["pts"] - s["media_time"] < c["end_ms"] for i in g["ids"])
        if allsel:
            gsel.append((gi, g["start"], g["end"], g["disp_min"], g["disp_max"], g["ids"][0] == g["ids"][0]))
    print("   fully selected gops", [(x[1],x[2],x[3],x[4]) for x in gsel])
    print("   out pts", [cum + f["disp"] - c["start_ms"] for f in disp])
    cum += c["end_ms"] - c["start_ms"]
print("total out", cum)
