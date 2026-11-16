#!/usr/bin/env python3
"""Tests for auto.py: wrap-around angles, mute gaps, overlapping objects,
seek/sequential consistency, spline, step boundaries, master clamping,
and a fuzz comparison against an independent reference implementation."""
import bisect
import json
import math
import os
import random
import subprocess
import sys
import tempfile

import auto

NEG_INF = float("-inf")
FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("ok   %s" % name)
    else:
        print("FAIL %s  %s" % (name, detail))
        FAILURES.append(name)


def approx(a, b, tol):
    return abs(a - b) <= tol


# ---------------------------------------------------------------------------
# Independent reference implementation, written directly from the README.
# ---------------------------------------------------------------------------

def ref_gain(g):
    if g is None:
        return NEG_INF
    return float(g)


def ref_lin(db):
    return 0.0 if db == NEG_INF else 10.0 ** (db / 20.0)


def ref_db(x):
    return NEG_INF if x <= 0.0 else 20.0 * math.log10(x)


def ref_lerp_gain(g0, g1, f):
    if g0 == NEG_INF or g1 == NEG_INF:
        return ref_db((1.0 - f) * ref_lin(g0) + f * ref_lin(g1))
    return g0 + f * (g1 - g0)


def ref_cr(a, b, c, d, f):
    return 0.5 * ((2 * b) + (c - a) * f
                  + (2 * a - 5 * b + 4 * c - d) * f * f
                  + (3 * b - a - 3 * c + d) * f * f * f)


def ref_wrap(d):
    return (d + 180.0) % 360.0 - 180.0


def ref_obj(obj, t):
    pts = obj["points"]
    ts = [p[0] for p in pts]
    mode = obj["interp"]
    if mode == "step":
        k = max(bisect.bisect_right(ts, t) - 1, 0)
        p = pts[k]
        return p[1], p[2], ref_gain(p[3])
    if t < ts[0]:
        p = pts[0]
        return p[1], p[2], ref_gain(p[3])
    if t >= ts[-1]:
        p = pts[-1]
        return p[1], p[2], ref_gain(p[3])
    i = bisect.bisect_right(ts, t) - 1
    while ts[i + 1] <= t:
        i += 1
    p0, p1 = pts[i], pts[i + 1]
    f = (t - p0[0]) / (p1[0] - p0[0])
    if mode == "linear":
        az = ref_wrap(p0[1] + f * ref_wrap(p1[1] - p0[1]))
        el = p0[2] + f * (p1[2] - p0[2])
        g = ref_lerp_gain(ref_gain(p0[3]), ref_gain(p1[3]), f)
        return az, el, g
    # spline: Catmull-Rom, endpoints duplicated
    q0 = pts[i - 1] if i > 0 else p0
    q3 = pts[i + 2] if i + 2 < len(pts) else p1
    u1 = p0[1]
    u0 = u1 + ref_wrap(q0[1] - u1)
    u2 = u1 + ref_wrap(p1[1] - u1)
    u3 = u2 + ref_wrap(q3[1] - u2)
    az = ref_wrap(ref_cr(u0, u1, u2, u3, f))
    el = ref_cr(q0[2], p0[2], p1[2], q3[2], f)
    gs = [ref_gain(q[3]) for q in (q0, p0, p1, q3)]
    if any(g == NEG_INF for g in gs):
        g = ref_db(max(ref_cr(*[ref_lin(x) for x in gs], f), 0.0))
    else:
        g = ref_cr(gs[0], gs[1], gs[2], gs[3], f)
    return az, el, g


def ref_master(curve, t):
    if t <= curve[0][0]:
        return ref_gain(curve[0][1])
    if t >= curve[-1][0]:
        return ref_gain(curve[-1][1])
    ts = [p[0] for p in curve]
    j = bisect.bisect_right(ts, t) - 1
    while ts[j + 1] <= t:
        j += 1
    t0, t1 = curve[j][0], curve[j + 1][0]
    f = (t - t0) / (t1 - t0)
    return ref_lerp_gain(ref_gain(curve[j][1]), ref_gain(curve[j + 1][1]), f)


def ref_render(d):
    sr, fs, nf = d["sample_rate"], d["frame_samples"], d["n_frames"]
    frames, master_log = [], []
    for i in range(nf):
        t = i * fs * 1000.0 / sr
        mg = ref_master(d["master_curve"], t)
        master_log.append(round(ref_lin(mg), 6))
        row = []
        for obj in d["objects"]:
            az, el, g = ref_obj(obj, t)
            row.append({"id": obj["id"], "az": round(az, 3),
                        "el": round(el, 3),
                        "lin_gain": round(ref_lin(g) * ref_lin(mg), 6)})
        frames.append(row)
    return {"frames": frames, "applied_master": master_log}


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

def make_input(objects, master, sr=1000, fs=1, nf=101):
    return {"sample_rate": sr, "frame_samples": fs, "n_frames": nf,
            "objects": objects, "master_curve": master}


def test_wrap_shortest_arc():
    # 170 -> -170 must sweep 20 deg through +/-180, not 340 deg
    d = make_input([{"id": "a", "interp": "linear",
                     "points": [[0, 170, 0, 0], [100, -170, 0, 0]]}],
                   [[0, 0]])
    out = auto.render(d)
    fr = out["frames"]
    check("wrap start", fr[0][0]["az"] == 170.0, fr[0][0])
    check("wrap q1", fr[25][0]["az"] == 175.0, fr[25][0])
    check("wrap mid lands on +/-180", abs(fr[50][0]["az"]) == 180.0, fr[50][0])
    check("wrap q3", fr[75][0]["az"] == -175.0, fr[75][0])
    check("wrap end clamped", fr[100][0]["az"] == -170.0, fr[100][0])
    # monotonic along the short arc: unwrapped values increase by 0.2/frame
    prev = 170.0
    ok = True
    for i in range(1, 101):
        az = fr[i][0]["az"]
        unwrapped = prev + auto.wrap_delta(az - prev)
        if not approx(unwrapped - prev, 0.2, 1e-3):
            ok = False
        prev = unwrapped
    check("wrap monotonic 0.2deg/frame", ok)


def test_step_boundary_and_hold():
    d = make_input([{"id": "s", "interp": "step",
                     "points": [[0, 10, 5, 0], [50, 20, -5, -6]]}],
                   [[0, 0]], nf=80)
    fr = auto.render(d)["frames"]
    check("step holds old before boundary",
          fr[49][0]["az"] == 10.0 and fr[49][0]["el"] == 5.0, fr[49][0])
    # point exactly ON the frame boundary belongs to the NEW value
    check("step new value exactly on boundary",
          fr[50][0]["az"] == 20.0 and fr[50][0]["el"] == -5.0, fr[50][0])
    check("step gain in dB",
          approx(fr[50][0]["lin_gain"], round(10 ** (-6 / 20), 6), 1e-6),
          fr[50][0])
    check("step holds through gap (block gap keeps last value)",
          fr[79][0]["az"] == 20.0, fr[79][0])


def test_step_before_first_point():
    d = make_input([{"id": "s", "interp": "step",
                     "points": [[50, 7, 0, -3]]}],
                   [[0, 0]], nf=80)
    fr = auto.render(d)["frames"]
    check("step before first point uses first value (not silence)",
          fr[0][0]["az"] == 7.0 and fr[0][0]["lin_gain"] != 0.0, fr[0][0])


def test_spline_values():
    pts = [[0, 0, 0, 0], [100, 0, 10, 0], [200, 0, 10, 0], [300, 0, 0, 0]]
    d = make_input([{"id": "sp", "interp": "spline", "points": pts}],
                   [[0, 0]], nf=301)
    fr = auto.render(d)["frames"]
    # passes through control points
    check("spline through p1", fr[100][0]["el"] == 10.0, fr[100][0])
    check("spline through p2", fr[200][0]["el"] == 10.0, fr[200][0])
    # hand-computed Catmull-Rom with duplicated endpoints
    check("spline seg0 mid (duplicated endpoint)",
          approx(fr[50][0]["el"], 5.0, 1e-3), fr[50][0])
    check("spline seg1 mid", approx(fr[150][0]["el"], 11.25, 1e-3), fr[150][0])
    check("spline seg2 mid", approx(fr[250][0]["el"], 5.0, 1e-3), fr[250][0])
    check("spline clamps before first point", fr[0][0]["el"] == 0.0, fr[0][0])


def test_spline_azimuth_wrap():
    d = make_input([{"id": "sp", "interp": "spline",
                     "points": [[0, 170, 0, 0], [100, -170, 0, 0]]}],
                   [[0, 0]])
    fr = auto.render(d)["frames"]
    check("spline az shortest arc mid", abs(fr[50][0]["az"]) == 180.0,
          fr[50][0])
    # Catmull-Rom with duplicated endpoints: CR(170,170,190,190,0.25)
    check("spline az q1", approx(fr[25][0]["az"], 174.062, 1e-3), fr[25][0])
    check("spline az q3", approx(fr[75][0]["az"], -174.062, 1e-3), fr[75][0])


def test_mute():
    # explicit mute (null gain) fading to 0 dB: linear-domain ramp
    d = make_input([{"id": "m", "interp": "linear",
                     "points": [[0, 0, 0, None], [100, 0, 0, 0]]}],
                   [[0, 0]])
    out = auto.render(d)
    fr = out["frames"]
    check("mute at muted point", fr[0][0]["lin_gain"] == 0.0, fr[0][0])
    check("mute ramp midpoint is linear-domain",
          approx(fr[50][0]["lin_gain"], 0.5, 1e-6), fr[50][0])
    check("mute ramp quarter", approx(fr[25][0]["lin_gain"], 0.25, 1e-6),
          fr[25][0])
    # muted master
    d2 = make_input([{"id": "x", "interp": "linear",
                      "points": [[0, 0, 0, 0]]}],
                    [[0, None], [100, 0]])
    out2 = auto.render(d2)
    check("muted master gives 0", out2["applied_master"][0] == 0.0,
          out2["applied_master"][:3])
    check("muted master ramps in linear domain",
          approx(out2["applied_master"][50], 0.5, 1e-6),
          out2["applied_master"][50])
    # step object holding a muted value across a gap
    d3 = make_input([{"id": "s", "interp": "step",
                      "points": [[0, 0, 0, 0], [40, 0, 0, None],
                                 [80, 0, 0, 0]]}],
                    [[0, 0]], nf=120)
    fr3 = auto.render(d3)["frames"]
    check("step mute gap silent", fr3[60][0]["lin_gain"] == 0.0, fr3[60][0])
    check("step unmutes at next point", fr3[80][0]["lin_gain"] == 1.0,
          fr3[80][0])


def test_master_clamp_and_interp():
    # regression: before the first master point the FIRST value applies
    d = make_input([{"id": "x", "interp": "linear",
                     "points": [[0, 0, 0, 0]]}],
                   [[50, -6], [100, 0]], nf=150)
    out = auto.render(d)
    check("master clamps to first value before range",
          out["applied_master"][0] == round(10 ** (-6 / 20), 6),
          out["applied_master"][:2])
    check("master clamps to last value after range",
          out["applied_master"][149] == 1.0, out["applied_master"][-1])
    check("master linear in dB at midpoint",
          approx(out["applied_master"][75], round(10 ** (-3 / 20), 6), 1e-6),
          out["applied_master"][75])


def test_overlapping_objects():
    objs = [
        {"id": "lin", "interp": "linear",
         "points": [[0, -170, 0, 0], [100, 170, 0, -6]]},
        {"id": "stp", "interp": "step",
         "points": [[10, 45, 10, -12], [60, -45, -10, 0]]},
        {"id": "spl", "interp": "spline",
         "points": [[0, 0, 0, 0], [50, 90, 20, -6], [100, 0, 0, -12]]},
    ]
    d = make_input(objs, [[0, -3], [100, 0]])
    got = auto.render(d)
    want = ref_render(d)
    ok = True
    for i in range(d["n_frames"]):
        for k in range(3):
            g, w = got["frames"][i][k], want["frames"][i][k]
            if not (approx(g["az"], w["az"], 1e-3)
                    and approx(g["el"], w["el"], 1e-3)
                    and approx(g["lin_gain"], w["lin_gain"], 1e-6)):
                ok = False
    check("overlapping objects match reference per frame", ok)
    check("overlap master matches",
          all(approx(a, b, 1e-6)
              for a, b in zip(got["applied_master"], want["applied_master"])))


def test_seek_consistency():
    # sequential render vs random-access ("reverse enter") evaluation
    objs = [
        {"id": "a", "interp": "linear",
         "points": [[0, 170, 0, 0], [37, -170, 5, -6], [80, 0, 0, None]]},
        {"id": "b", "interp": "step",
         "points": [[5, 10, 0, 0], [55, 20, 0, -9]]},
        {"id": "c", "interp": "spline",
         "points": [[0, 0, 0, 0], [30, 45, 10, -3], [60, -45, 0, -6],
                    [90, 0, -10, 0]]},
    ]
    d = make_input(objs, [[0, 0], [50, -6], [100, 0]], sr=48000, fs=480,
                   nf=10)
    seq = auto.render(d)
    order = list(range(d["n_frames"]))
    random.seed(7)
    random.shuffle(order)
    order += list(reversed(range(d["n_frames"])))
    ok = True
    for i in order:
        row, mg, _, _ = auto.eval_frame(d, i)
        if row != seq["frames"][i] or mg != seq["applied_master"][i]:
            ok = False
    check("seek/reverse enter matches sequential render", ok)


def test_diagnostics():
    d = make_input([{"id": "s", "interp": "step",
                     "points": [[0, 0, 0, 0], [50, 10, 0, 0]]}],
                   [[0, 0], [100, -6]], sr=1000, fs=8, nf=101)
    out = auto.render(d)
    tr = out["diagnostics"]["transitions"]
    step_tr = [e for e in tr if e["id"] == "s"]
    check("diag initial entry at frame 0",
          step_tr[0]["frame"] == 0 and step_tr[0]["sample"] == 0, step_tr)
    # fs=8 @ 1000 Hz -> frame i starts at 8i ms; the t=50ms point first
    # takes effect at frame 7 (t=56ms), i.e. absolute sample 56
    sw = [e for e in step_tr if e["segment"] == 1]
    check("diag step switch frame (first frame at/after the point)",
          len(sw) == 1 and sw[0]["frame"] == 7, step_tr)
    check("diag effective sample index = frame*frame_samples",
          sw[0]["sample"] == 7 * 8, sw)
    mtr = [e for e in tr if e["id"] == "master"]
    check("diag master segment transition logged",
          any(e["segment"] == 0 and e["frame"] == 0 for e in mtr), mtr)
    check("diag carries interpolation parameter f",
          all("f" in e for e in tr), tr[:2])


def test_fuzz_against_reference():
    rng = random.Random(1234)
    ok = True
    for trial in range(60):
        sr = rng.choice([8000, 44100, 48000])
        fs = rng.choice([1, 64, 480, 1024])
        nf = rng.randint(20, 120)
        tmax = nf * fs * 1000.0 / sr
        objs = []
        for k in range(rng.randint(1, 4)):
            npts = rng.randint(1, 6)
            times = sorted(rng.uniform(-10, tmax + 10) for _ in range(npts))
            pts = []
            for tp in times:
                gain = rng.choice([None, rng.uniform(-60, 12)])
                pts.append([round(tp, 3), rng.uniform(-180, 180),
                            rng.uniform(-30, 90), gain])
            objs.append({"id": "o%d" % k,
                         "interp": rng.choice(["linear", "step", "spline"]),
                         "points": pts})
        nm = rng.randint(1, 4)
        master = [[round(rng.uniform(-5, tmax + 5), 3),
                   rng.choice([None, rng.uniform(-40, 6)])]
                  for _ in range(nm)]
        master.sort(key=lambda p: p[0])
        d = {"sample_rate": sr, "frame_samples": fs, "n_frames": nf,
             "objects": objs, "master_curve": master}
        got = auto.render(d)
        want = ref_render(d)
        for i in range(nf):
            if not approx(got["applied_master"][i],
                          want["applied_master"][i], 1e-6):
                ok = False
            for g, w in zip(got["frames"][i], want["frames"][i]):
                if not (approx(g["az"], w["az"], 1e-3)
                        and approx(g["el"], w["el"], 1e-3)
                        and approx(g["lin_gain"], w["lin_gain"], 1e-6)):
                    ok = False
                    print("  fuzz mismatch trial %d frame %d: %s vs %s"
                          % (trial, i, g, w))
    check("fuzz: renderer matches independent reference", ok)


def test_cli_roundtrip():
    d = make_input([{"id": "a", "interp": "linear",
                     "points": [[0, 170, 0, 0], [100, -170, 0, -6]]}],
                   [[0, 0], [100, -6]])
    tmp = tempfile.mkdtemp(dir=".")
    inp = os.path.join(tmp, "in.json")
    outp = os.path.join(tmp, "out.json")
    with open(inp, "w") as fh:
        json.dump(d, fh)
    subprocess.run([sys.executable, "auto.py", inp, outp], check=True)
    with open(outp) as fh:
        out = json.load(fh)
    check("cli output keys",
          set(out) >= {"frames", "applied_master", "diagnostics"},
          sorted(out))
    check("cli frame count", len(out["frames"]) == d["n_frames"])
    check("cli matches in-process render",
          out["frames"] == auto.render(d)["frames"])


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    test_wrap_shortest_arc()
    test_step_boundary_and_hold()
    test_step_before_first_point()
    test_spline_values()
    test_spline_azimuth_wrap()
    test_mute()
    test_master_clamp_and_interp()
    test_overlapping_objects()
    test_seek_consistency()
    test_diagnostics()
    test_fuzz_against_reference()
    test_cli_roundtrip()
    if FAILURES:
        print("\n%d FAILURES: %s" % (len(FAILURES), FAILURES))
        sys.exit(1)
    print("\nall tests passed")


if __name__ == "__main__":
    main()


