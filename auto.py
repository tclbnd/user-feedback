#!/usr/bin/env python3
import json, sys


def lerp(t, p0, p1):
    if p1[0] == p0[0]:
        return p0[1], p0[2], p0[3]
    f = (t - p0[0]) / (p1[0] - p0[0])
    return (p0[1] + f * (p1[1] - p0[1]), p0[2] + f * (p1[2] - p0[2]),
            p0[3] + f * (p1[3] - p0[3]))


def main():
    d = json.load(open(sys.argv[1]))
    sr, fs, nf = d["sample_rate"], d["frame_samples"], d["n_frames"]
    frames = []; master_log = []
    for i in range(nf):
        t = i * fs * 1000 / sr
        mg = d["master_curve"][-1][1]
        for j in range(len(d["master_curve"]) - 1):
            p0, p1 = d["master_curve"][j], d["master_curve"][j + 1]
            if p0[0] <= t <= p1[0]:
                mg = p0[1] + (t - p0[0]) / (p1[0] - p0[0]) * (p1[1] - p0[1]); break
        master_log.append(round(10 ** (mg / 20), 6))
        row = []
        for obj in d["objects"]:
            pts = obj["points"]
            az, el, g = pts[0][1], pts[0][2], pts[0][3]
            if obj["interp"] == "step":
                p = pts[0]
                for q in pts:
                    if q[0] <= t: p = q
                az, el, g = p[1], p[2], p[3]
            else:
                # defect: linear path used for spline too; azimuth not wrapped
                for j in range(len(pts) - 1):
                    if pts[j][0] <= t <= pts[j + 1][0]:
                        az, el, g = lerp(t, pts[j], pts[j + 1]); break
                else:
                    if t > pts[-1][0]:
                        az, el, g = pts[-1][1], pts[-1][2], pts[-1][3]
            row.append({"id": obj["id"], "az": round(az, 3), "el": round(el, 3),
                        "lin_gain": round(10 ** ((g + mg) / 20), 6)})
        frames.append(row)
    json.dump({"frames": frames, "applied_master": master_log},
              open(sys.argv[2], "w"))

main()
