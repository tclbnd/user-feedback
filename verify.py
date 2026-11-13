#!/usr/bin/env python3
"""Acceptance checks for atmo.py.

Covers:
  A1 transmittance vs analytic optical depth (constant-density approx and
     high-altitude thin/vertical paths), rel err <= 0.5%
  A2 inscatter vs high-sample numerical integration, rel err <= 3%
  B1 build texel order (forward/reverse/strided) -> identical tables
  B2 query continuity at table boundaries and clamping outside the table
  B3 byte-identical reproducibility of two full CLI builds
"""
import json, math, os, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import atmo

CFG = {"lut_size": 16, "samples": 32, "seed": 1}
FAIL = []


def rel(a, b):
    return abs(a - b) / max(abs(b), 1e-30)


def check(name, ok, detail=""):
    print("%s %-58s %s" % ("PASS" if ok else "FAIL", name, detail))
    if not ok:
        FAIL.append(name)


def a1_transmittance():
    print("== A1: transmittance vs analytic optical depth (tol 0.5%) ==")
    # --- constant-density approximation: shrink scale height effect by
    # temporarily using a huge H so rho ~ 1; tau must equal BETA * path
    # length through the spherical shell (exact ray-sphere length).
    real_H = atmo.H
    atmo.H = 1e9
    try:
        for cz, alt in [(1.0, 0.0), (0.5, 10.0), (0.2, 30.0), (0.05, 0.0),
                        (-0.1, 40.0)]:
            r0, rt = atmo.R_EF + alt, atmo.R_EF + atmo.TOP
            length = -r0 * cz + math.sqrt(r0 * r0 * cz * cz + rt * rt - r0 * r0)
            tau_ex = atmo.BETA * length
            tau_num = atmo.optical_depth(cz, alt, 64)
            e = rel(tau_num, tau_ex)
            check("const-density tau cz=%.2f alt=%.1f" % (cz, alt),
                  e <= 5e-3, "rel_err=%.2e" % e)
    finally:
        atmo.H = real_H
    # --- vertical exponential path: analytic tau = BETA*H*(e^-alt/H - e^-TOP/H)
    for alt in [0.0, 10.0, 25.0, 45.0, 58.0]:
        tau_ex = atmo.BETA * atmo.H * (math.exp(-alt / atmo.H)
                                       - math.exp(-atmo.TOP / atmo.H))
        t_ex = math.exp(-tau_ex)
        t_num = atmo.transmittance(1.0, alt, CFG["samples"])
        e = rel(t_num, t_ex)
        check("vertical T alt=%.1f" % alt, e <= 5e-3,
              "rel_err=%.2e" % e)
    # --- high-altitude thin slant path vs high-sample reference
    for cz, alt in [(0.3, 40.0), (0.1, 50.0), (0.7, 55.0)]:
        t_ref = atmo.transmittance(cz, alt, 20000)
        t_num = atmo.transmittance(cz, alt, CFG["samples"])
        e = rel(t_num, t_ref)
        check("thin slant T cz=%.2f alt=%.1f" % (cz, alt), e <= 5e-3,
              "rel_err=%.2e" % e)


def a2_inscatter(lut):
    print("== A2: inscatter vs high-sample numerical integration (tol 3%) ==")
    n = CFG["lut_size"]
    nodes = [(4, 0), (8, 4), (12, 10), (8, 10), (13, 2)]  # (i, j) texels
    for i, j in nodes:
        mu = 2.0 * i / (n - 1) - 1.0
        h = atmo.TOP * j / (n - 1)
        ref = atmo.inscatter(mu, h, 2048, sun_samples=256)
        got = lut["S"][i][j]
        e = rel(got, ref)
        check("S node mu=%+.4f h=%.1f" % (mu, h), e <= 3e-2,
              "rel_err=%.2e (ref=%.6g)" % (e, ref))
    # off-node query (bilinear interpolation included), informational + bound
    for mu, h in [(0.0, 16.0), (0.33, 30.0), (-0.4, 8.0)]:
        ref = atmo.inscatter(mu, h, 2048, sun_samples=256)
        got = atmo.query(lut, "S", mu, h)
        e = rel(got, ref)
        check("S query mu=%+.2f h=%.1f (interp)" % (mu, h), e <= 3e-2,
              "rel_err=%.2e (ref=%.6g)" % (e, ref))


def b1_order_independence():
    print("== B1: build order independence ==")
    base = None
    for order in ["forward", "reverse", "strided"]:
        cfg = dict(CFG, order=order)
        out = atmo.build(cfg)
        blob = json.dumps(out, sort_keys=True)
        if base is None:
            base = blob
        check("order=%s identical" % order, blob == base,
              "checksum=%s" % out["meta"]["checksum"])


def b2_query_boundary(lut):
    print("== B2: query boundary continuity / clamping ==")
    n = CFG["lut_size"]
    # clamping outside the table returns the boundary value
    ok = True
    for alt in [0.0, 23.0, 60.0]:
        ok &= atmo.query(lut, "T", 1.4, alt) == atmo.query(lut, "T", 1.0, alt)
        ok &= atmo.query(lut, "T", -1.4, alt) == atmo.query(lut, "T", -1.0, alt)
    for cz in [-0.9, 0.0, 0.9]:
        ok &= atmo.query(lut, "T", cz, 75.0) == atmo.query(lut, "T", cz, 60.0)
        ok &= atmo.query(lut, "T", cz, -5.0) == atmo.query(lut, "T", cz, 0.0)
        ok &= atmo.query(lut, "S", cz, 75.0) == atmo.query(lut, "S", cz, 60.0)
    check("clamping outside table", ok)
    # continuity across interior node planes (both coordinates, both tables);
    # eps small enough that 2*eps*max_slope << threshold -> a jump would
    # indicate a real discontinuity, not genuine slope
    eps = 1e-8
    worst = 0.0
    for name in ["T", "S"]:
        for i in range(1, n - 1):
            x = 2.0 * i / (n - 1) - 1.0
            for y in [0.0, 17.3, 44.0, 60.0]:
                d = abs(atmo.query(lut, name, x - eps, y)
                        - atmo.query(lut, name, x + eps, y))
                worst = max(worst, d)
        for j in range(1, n - 1):
            y = atmo.TOP * j / (n - 1)
            for x in [-0.77, 0.13, 0.91]:
                d = abs(atmo.query(lut, name, x, y - eps)
                        - atmo.query(lut, name, x, y + eps))
                worst = max(worst, d)
    check("continuity across node planes", worst < 1e-5,
          "max jump=%.3e" % worst)
    # dense sweep for jumps along a full row/column
    for name in ["T", "S"]:
        prev = atmo.query(lut, name, -1.0, 23.0)
        jump = 0.0
        for k in range(1, 2001):
            v = atmo.query(lut, name, -1.0 + 2.0 * k / 2000, 23.0)
            jump = max(jump, abs(v - prev))
            prev = v
        check("%s row sweep jump-free" % name, jump < 0.05,
              "max step=%.3e" % jump)


def b3_reproducibility():
    print("== B3: byte-identical CLI reproducibility ==")
    here = os.path.dirname(os.path.abspath(__file__))
    tmp = tempfile.mkdtemp()
    cfgp = os.path.join(tmp, "config.json")
    with open(cfgp, "w") as f:
        json.dump(CFG, f)
    blobs = []
    for tag in ["o1.json", "o2.json"]:
        out = os.path.join(tmp, tag)
        subprocess.run([sys.executable, os.path.join(here, "atmo.py"),
                        cfgp, out], check=True)
        with open(out, "rb") as f:
            blobs.append(f.read())
    check("two CLI builds byte-identical", blobs[0] == blobs[1],
          "%d bytes" % len(blobs[0]))
    out = json.loads(blobs[0].decode())
    check("checksum stable", out["meta"]["checksum"] ==
          atmo.build(CFG)["meta"]["checksum"], out["meta"]["checksum"])
    return out


def main():
    lut = b3_reproducibility()
    a1_transmittance()
    a2_inscatter(lut)
    b1_order_independence()
    b2_query_boundary(lut)
    print()
    if FAIL:
        print("FAILED: %d check(s): %s" % (len(FAIL), ", ".join(FAIL)))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
