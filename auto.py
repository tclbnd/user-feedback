#!/usr/bin/env python3
"""Object audio automation renderer.

Each frame is a pure function of its absolute time
(t = frame * frame_samples * 1000 / sample_rate ms), so sequential
playback and random-access seeks to the same time agree exactly.

Conventions beyond the README:
- Explicit mute: a gain of null / "-inf" / -Infinity is -inf dB
  (lin_gain 0). Interpolation involving a muted endpoint is done in
  the linear-gain domain; otherwise gains interpolate in dB.
- Angle interpolation (linear and spline) follows the shortest arc;
  interpolated azimuths are wrapped back to [-180, 180).
- Segments are half-open [t0, t1): a point exactly on a boundary
  belongs to the NEW segment (evaluate at t, never t-epsilon).
- "diagnostics.transitions" logs, per object (and "master"), the
  frame and absolute sample index at which a new segment/point takes
  effect, with the interpolation parameter f at that frame.
"""
import json, math, sys

NEG_INF = float("-inf")


def wrap_delta(d):
    """Shortest signed angular difference, in [-180, 180)."""
    return (d + 180.0) % 360.0 - 180.0


def wrap_angle(a):
    return (a + 180.0) % 360.0 - 180.0


def parse_gain(g):
    """Gain in dB; explicit mute maps to -inf dB."""
    if g is None:
        return NEG_INF
    if isinstance(g, str):
        s = g.strip().lower()
        if s in ("-inf", "-infinity", "mute", "muted"):
            return NEG_INF
        return float(s)
    g = float(g)
    return NEG_INF if math.isinf(g) and g < 0 else g


def lin_of(db):
    return 0.0 if db == NEG_INF else 10.0 ** (db / 20.0)


def db_of(lin):
    return NEG_INF if lin <= 0.0 else 20.0 * math.log10(lin)


def lerp_gain(g0, g1, f):
    """dB-domain lerp; a muted endpoint switches to linear-domain lerp."""
    if g0 == NEG_INF or g1 == NEG_INF:
        return db_of((1.0 - f) * lin_of(g0) + f * lin_of(g1))
    return g0 + f * (g1 - g0)


def catmull_rom(p0, p1, p2, p3, f):
    f2 = f * f
    f3 = f2 * f
    return 0.5 * ((2.0 * p1) + (-p0 + p2) * f
                  + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * f2
                  + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * f3)


def locate(pts, t):
    """Half-open segment search: returns ('clamp', idx, 0.0) outside the
    point range, else ('seg', i, f) with pts[i].t <= t < pts[i+1].t."""
    if t < pts[0][0]:
        return ("clamp", 0, 0.0)
    for i in range(len(pts) - 1):
        t0, t1 = pts[i][0], pts[i + 1][0]
        if t0 <= t < t1:
            return ("seg", i, (t - t0) / (t1 - t0))
    return ("clamp", len(pts) - 1, 0.0)


def eval_spline(pts, i, f):
    p1, p2 = pts[i], pts[i + 1]
    p0 = pts[i - 1] if i > 0 else p1          # endpoints duplicated
    p3 = pts[i + 2] if i + 2 < len(pts) else p2
    # azimuth: unwrap the 4 control points onto one continuous arc
    u1 = p1[1]
    u0 = u1 + wrap_delta(p0[1] - u1)
    u2 = u1 + wrap_delta(p2[1] - u1)
    u3 = u2 + wrap_delta(p3[1] - u2)
    az = wrap_angle(catmull_rom(u0, u1, u2, u3, f))
    el = catmull_rom(p0[2], p1[2], p2[2], p3[2], f)
    gs = [parse_gain(p[3]) for p in (p0, p1, p2, p3)]
    if any(g == NEG_INF for g in gs):
        g = db_of(max(catmull_rom(*[lin_of(x) for x in gs], f), 0.0))
    else:
        g = catmull_rom(gs[0], gs[1], gs[2], gs[3], f)
    return az, el, g


def eval_object(obj, t):
    """Returns (az, el, gain_db, (mode, segment_index, f))."""
    pts = obj["points"]
    interp = obj.get("interp", "linear")
    if interp == "step":
        p, idx = pts[0], 0
        for k, q in enumerate(pts):
            if q[0] <= t:
                p, idx = q, k
        return p[1], p[2], parse_gain(p[3]), ("step", idx, 0.0)
    kind, i, f = locate(pts, t)
    if kind == "clamp":
        p = pts[i]
        return p[1], p[2], parse_gain(p[3]), ("clamp", i, 0.0)
    p0, p1 = pts[i], pts[i + 1]
    if interp == "spline":
        az, el, g = eval_spline(pts, i, f)
    else:  # linear
        az = wrap_angle(p0[1] + f * wrap_delta(p1[1] - p0[1]))
        el = p0[2] + f * (p1[2] - p0[2])
        g = lerp_gain(parse_gain(p0[3]), parse_gain(p1[3]), f)
    return az, el, g, (interp, i, f)


def eval_master(curve, t):
    """Linear in dB, clamped to end values outside the curve range."""
    if t < curve[0][0]:
        return parse_gain(curve[0][1]), ("clamp", 0, 0.0)
    for j in range(len(curve) - 1):
        t0, t1 = curve[j][0], curve[j + 1][0]
        if t0 <= t < t1:
            f = (t - t0) / (t1 - t0)
            return lerp_gain(parse_gain(curve[j][1]),
                             parse_gain(curve[j + 1][1]), f), ("linear", j, f)
    return parse_gain(curve[-1][1]), ("clamp", len(curve) - 1, 0.0)


def eval_frame(d, i):
    sr, fs = d["sample_rate"], d["frame_samples"]
    t = i * fs * 1000.0 / sr
    mg, mloc = eval_master(d["master_curve"], t)
    row, locs = [], []
    for obj in d["objects"]:
        az, el, g, loc = eval_object(obj, t)
        row.append({"id": obj["id"], "az": round(az, 3), "el": round(el, 3),
                    "lin_gain": round(lin_of(g) * lin_of(mg), 6)})
        locs.append((obj["id"], loc))
    return row, round(lin_of(mg), 6), locs, mloc


def render(d):
    nf, fs = d["n_frames"], d["frame_samples"]
    frames, master_log, transitions = [], [], []
    prev = {}
    for i in range(nf):
        row, mg_lin, locs, mloc = eval_frame(d, i)
        frames.append(row)
        master_log.append(mg_lin)
        entries = list(locs)
        entries.append(("master", mloc))
        for oid, loc in entries:
            sig = (loc[0], loc[1])
            if prev.get(oid) != sig:
                transitions.append({"frame": i, "sample": i * fs, "id": oid,
                                    "mode": loc[0], "segment": loc[1],
                                    "f": round(loc[2], 6)})
                prev[oid] = sig
    return {"frames": frames, "applied_master": master_log,
            "diagnostics": {"transitions": transitions}}


def main():
    d = json.load(open(sys.argv[1]))
    json.dump(render(d), open(sys.argv[2], "w"))


if __name__ == "__main__":
    main()
