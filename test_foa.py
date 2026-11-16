#!/usr/bin/env python3
"""Acceptance tests for foa.py per README spec."""
import json, math, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FOA = os.path.join(HERE, "foa.py")

def op(kind, deg):
    return dict(kind=kind, deg=deg)

def run(channels, ops, sn3d=True):
    inp = dict(channels=channels, ops=ops, sn3d=sn3d)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(inp, f); fi = f.name
    fo = fi + ".out"
    subprocess.run([sys.executable, FOA, fi, fo], check=True)
    out = json.load(open(fo))
    os.unlink(fi); os.unlink(fo)
    return out

def az_el(ch):
    """decode direction of unit-norm SN3D triple given as [Y,Z,X]."""
    Y, Z, X = ch
    r = math.sqrt(X*X + Y*Y + Z*Z)
    az = math.degrees(math.atan2(Y, X))
    el = math.degrees(math.asin(max(-1.0, min(1.0, Z / r)))) if r else 0.0
    return az, el

fails = []
def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("  " + info if info else ""))
    if not cond: fails.append(name)

# ---- 1. fixed front/back/left/right virtual sources (ACN [W,Y,Z,X]) ----
srcs = dict(front=[1,0,0,1], left=[1,1,0,0], back=[1,0,0,-1], right=[1,-1,0,0])
expect_yaw90 = dict(front="left", left="back", back="right", right="front")
for name, ch in srcs.items():
    out = run([[v] for v in ch], [op("yaw", 90)])
    got = [out["channels"][i][0] for i in range(4)]
    exp = srcs[expect_yaw90[name]]
    check("yaw+90 %s->%s" % (name, expect_yaw90[name]),
          all(abs(a-b) < 1e-9 for a, b in zip(got, exp)), "got %s" % got)
    check("yaw+90 %s W untouched" % name, got[0] == 1.0)

up = [1,0,1,0]
out = run([[v] for v in srcs["front"]], [op("pitch", 90)])
got = [out["channels"][i][0] for i in range(4)]
check("pitch+90 front->up", all(abs(a-b)<1e-9 for a,b in zip(got, up)), "got %s" % got)
out = run([[v] for v in up], [op("pitch", 90)])
got = [out["channels"][i][0] for i in range(4)]
check("pitch+90 up->back", all(abs(a-b)<1e-9 for a,b in zip(got, srcs["back"])), "got %s" % got)

out = run([[v] for v in srcs["left"]], [op("roll", 90)])
got = [out["channels"][i][0] for i in range(4)]
check("roll+90 left->up", all(abs(a-b)<1e-9 for a,b in zip(got, up)), "got %s" % got)
out = run([[v] for v in up], [op("roll", 90)])
got = [out["channels"][i][0] for i in range(4)]
check("roll+90 up->right", all(abs(a-b)<1e-9 for a,b in zip(got, srcs["right"])), "got %s" % got)

# ---- 2. continuous turning: per-sample direction angle ----
N = 721
az0 = 30.0
ch = [[0.5]*N, [math.sin(math.radians(az0))]*N, [0.0]*N, [math.cos(math.radians(az0))]*N]
out = run(ch, [op("yaw", 0.5)]*180)
ok = True
for i in [0, 1, 100, 360, 720]:
    az, el = az_el([out["channels"][1][i], out["channels"][2][i], out["channels"][3][i]])
    if abs(az - (az0+90)) > 1e-4 or abs(el) > 1e-4: ok = False
check("continuous 180x0.5deg yaw -> az+90 at sampled instants", ok)
out = run(ch, [op("yaw", 1)]*360)
ok = all(abs(out["channels"][c][i] - round(ch[c][i], 6)) < 1e-9
         for c in range(4) for i in range(N))
check("360x1deg yaw returns to start (vs round6 input, 1e-9)", ok)

# ---- 3. opposite-sign keyframes, same pose ----
base = [[0.7,0.3,-0.1], [0.2,-0.5,0.4], [0.1,0.6,-0.3], [0.6,-0.2,0.5]]
for kind in ["yaw", "pitch", "roll"]:
    a = run([c[:] for c in base], [op(kind, 180)])
    b = run([c[:] for c in base], [op(kind, -180)])
    check(kind + " +180 == -180", a["channels"] == b["channels"])
out = run([c[:] for c in base], [op("yaw",180), op("pitch",180), op("roll",180)])
ok = all(abs(out["channels"][c][i] - base[c][i]) < 1e-9 for c in range(4) for i in range(3))
check("yaw180+pitch180+roll180 == identity (q/-q same pose)", ok)
out = run([c[:] for c in base], [op("yaw",37.5), op("yaw",-37.5)])
ok = all(abs(out["channels"][c][i] - base[c][i]) < 1e-9 for c in range(4) for i in range(3))
check("yaw +37.5 then -37.5 cancels", ok)

# ---- 4. energy preservation (total, 1e-9 relative) ----
import random
random.seed(7)
N = 500
ch = [[random.uniform(-1,1) for _ in range(N)] for _ in range(4)]
ops = [op("yaw",33.3), op("pitch",-57.1), op("roll",12.9), op("yaw",-110.0)]
out = run([c[:] for c in ch], ops)
e_in = sum(sum(v*v for v in c) for c in ch)
e_out = sum(out["energy"])
rel = abs(e_out - e_in)/e_in
check("total energy preserved (rel %.2e < 1e-9)" % rel, rel < 1e-9)
w_in = sum(v*v for v in ch[0])
check("W energy untouched", abs(out["energy"][0]-w_in) < 1e-6)

# ---- 5. sn3d:false normalization round-trips exactly ----
a = run([c[:] for c in ch], ops, sn3d=True)
b = run([c[:] for c in ch], ops, sn3d=False)
check("sn3d:false == sn3d:true outputs", a["channels"] == b["channels"])
out = run([c[:] for c in ch], [], sn3d=False)
check("sn3d:false zero-op exact round-trip",
      all(out["channels"][c] == [round(v,6) for v in ch[c]] for c in range(4)))

# ---- 6. block size / start-offset invariance ----
full = run([c[:] for c in ch], ops)
for split in [1, 7, 128, 499]:
    parts = []
    for s in range(0, N, split):
        seg = [c[s:s+split] for c in ch]
        parts.append(run(seg, ops)["channels"])
    joined = [[v for p in parts for v in p[c]] for c in range(4)]
    check("block split=%d identical to full run" % split, joined == full["channels"])

print()
if fails:
    print("FAILURES:", fails); sys.exit(1)
print("ALL TESTS PASSED")
