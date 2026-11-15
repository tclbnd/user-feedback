"""Acceptance tests for ssim.py (pure stdlib; run: python3 test_ssim.py).

Covers:
  T1 window kernels: gauss 11 taps sigma=1.5 sum-1; uniform 8x8 sum-1.
  T2 constants C1/C2/C3 and basic SSIM properties (identical images -> 1).
  T3 independent naive O(N*K^2) reference: per-image mean SSIM error <=0.005
     and per-pixel map error <=0.005 for both window shapes.
  T4 tiled runs block=2/4 reproduce block=1 maps pixel-exactly (bit compare),
     both windows, single-scale and multi-scale.
  T5 multi-scale: downsample-before-stats order, odd-size truncation, per_scale
     length, last-scale map equals a direct single-scale call on the smallest
     level; CS maps on coarser levels use C3.
  T6 boundary convention: valid (no padding) vs symmetric-padded variant; the
     discrepancy is explained and stays <= 0.01 on interior region.
  T7 CLI contract python3 ssim.py in.json out.json incl. digest stability.
  T8 regression: defective baseline differs materially from the fixed code.
"""

import json
import math
import os
import random
import subprocess
import sys

import ssim as S

# Sandbox-safe scratch area (system temp dir may not be writable here).
TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "tmp", "ssim_test")
os.makedirs(TMP, exist_ok=True)

FAIL = []


def check(cond, msg):
    status = "PASS" if cond else "FAIL"
    print("  [%s] %s" % (status, msg))
    if not cond:
        FAIL.append(msg)


# ---------------------------------------------------------------- datasets
def make_ref(seed, h, w):
    rnd = random.Random(seed)
    base = [[rnd.uniform(40.0, 215.0) for _ in range(w)] for _ in range(h)]
    # smooth with a 3x3 box to create spatial structure, then re-seed noise
    out = [[0.0] * w for _ in range(h)]
    for i in range(h):
        for j in range(w):
            acc, n = 0.0, 0
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    y, x = i + di, j + dj
                    if 0 <= y < h and 0 <= x < w:
                        acc += base[y][x]
                        n += 1
            out[i][j] = acc / n
    return out


def distort(ref, kind, seed):
    rnd = random.Random(seed * 7919 + 13)
    h, w = len(ref), len(ref[0])
    out = [row[:] for row in ref]
    if kind == "gnoise":
        for i in range(h):
            for j in range(w):
                out[i][j] += rnd.gauss(0.0, 18.0)
    elif kind == "spnoise":
        for _ in range(h * w // 25):
            y, x = rnd.randrange(h), rnd.randrange(w)
            out[y][x] = 0.0 if rnd.random() < 0.5 else 255.0
    elif kind == "gblur":
        tmp = [row[:] for row in out]
        for i in range(1, h - 1):
            for j in range(1, w - 1):
                tmp[i][j] = (out[i - 1][j] + out[i + 1][j]
                             + out[i][j - 1] + out[i][j + 1]) * 0.25
        out = tmp
    elif kind == "contrast":
        f = rnd.uniform(0.6, 0.9)
        out = [[128.0 + (v - 128.0) * f for v in row] for row in out]
    elif kind == "bright":
        d = rnd.uniform(-25.0, 25.0)
        out = [[v + d for v in row] for row in out]
    elif kind == "blockshift":
        for _ in range(4):
            by, bx = rnd.randrange(0, h - 8), rnd.randrange(0, w - 8)
            d = rnd.uniform(-40.0, 40.0)
            for i in range(by, min(by + 8, h)):
                for j in range(bx, min(bx + 8, w)):
                    out[i][j] += d
    elif kind == "jpegish":
        # coarse quantisation on 8x8 blocks
        q = rnd.choice([16, 24, 32])
        for i in range(h):
            for j in range(w):
                out[i][j] = round(out[i][j] / q) * q
    elif kind == "mix":
        out = distort(ref, "gnoise", seed)
        out = distort(out, "contrast", seed + 1)
    return [[min(255.0, max(0.0, v)) for v in row] for row in out]


KINDS = ["gnoise", "spnoise", "gblur", "contrast", "bright",
         "blockshift", "jpegish", "mix"]

# 20 deterministic pairs; sizes include odd and even heights/widths.
# Min 48x48 guarantees the 3-scale smallest level (>=12 per side) fits the
# 11-tap gaussian window after two truncating halvings.
PAIRS = []
for k in range(20):
    seed = 1000 + k
    h = 48 + (k * 7) % 17          # 48..64
    w = 48 + (k * 11) % 15         # 48..62
    ref = make_ref(seed, h, w)
    dist = distort(ref, KINDS[k % len(KINDS)], seed)
    PAIRS.append((ref, dist))


# ------------------------------------------------- independent naive reference
def gauss2d(taps=11, sigma=1.5):
    r = taps // 2
    g = [[math.exp(-((i - r) ** 2 + (j - r) ** 2) / (2.0 * sigma * sigma))
          for j in range(taps)] for i in range(taps)]
    s = sum(sum(row) for row in g)
    return [[v / s for v in row] for row in g]


def naive_ssim_map(A, B, mode):
    """Brute-force per-pixel weighted window; deliberately separate code."""
    h, w = len(A), len(A[0])
    if mode == "gauss":
        win, k = gauss2d(), 11
    else:
        win, k = [[1.0 / 64.0] * 8 for _ in range(8)], 8
    oh, ow = h - k + 1, w - k + 1
    out = [[0.0] * ow for _ in range(oh)]
    for i in range(oh):
        for j in range(ow):
            ma = mb = va = vb = cv = wsum = 0.0
            for u in range(k):
                for v in range(k):
                    ww = win[u][v]
                    ma += ww * A[i + u][j + v]
                    mb += ww * B[i + u][j + v]
                    wsum += ww
            for u in range(k):
                for v in range(k):
                    ww = win[u][v]
                    da = A[i + u][j + v] - ma
                    db = B[i + u][j + v] - mb
                    va += ww * da * da
                    vb += ww * db * db
                    cv += ww * da * db
            va = max(va, 0.0)
            vb = max(vb, 0.0)
            cv = max(cv, 0.0)
            lum = (2 * ma * mb + S.C1) / (ma * ma + mb * mb + S.C1)
            cs = (2 * cv + S.C2) / (va + vb + S.C2)
            out[i][j] = lum * cs
    return out


def reflect_pad(img, r):
    """Symmetric edge replication by r pixels on every side."""
    h, w = len(img), len(img[0])
    out = [[0.0] * (w + 2 * r) for _ in range(h + 2 * r)]
    for i in range(h + 2 * r):
        si = max(0, min(h - 1, i - r))
        for j in range(w + 2 * r):
            sj = max(0, min(w - 1, j - r))
            out[i][j] = img[si][sj]
    return out


print("== T1 window kernels ==")
gk = S.gauss_1d()
check(len(gk) == 11, "gauss kernel has 11 taps")
check(abs(sum(gk) - 1.0) < 1e-12, "gauss kernel sums to 1")
check(abs(gk[0] - gk[-1]) < 1e-15 and gk[5] == max(gk), "gauss symmetric peak")
uk = S.uniform_1d()
check(len(uk) == 8 and all(abs(v - 0.125) < 1e-15 for v in uk),
      "uniform kernel is 8 equal taps of 1/8")
g2 = gauss2d()
check(abs(sum(sum(r) for r in g2) - 1.0) < 1e-12, "2D gauss sums to 1")

print("== T2 constants and basic properties ==")
check(abs(S.C1 - 6.5025) < 1e-12 and abs(S.C2 - 58.5225) < 1e-12
      and abs(S.C3 - S.C2 / 2) < 1e-12, "C1=6.5025 C2=58.5225 C3=C2/2")
ref0 = PAIRS[0][0]
res_same = S.compute({"ref": ref0, "dist": ref0, "window": "gauss"})
check(abs(res_same["ssim"] - 1.0) < 1e-12,
      "identical images -> SSIM 1.0 (got %.12f)" % res_same["ssim"])
check(all(abs(v - 1.0) < 1e-12 for row in res_same["map"] for v in row),
      "identical images -> map all 1.0")
for pair in PAIRS[:5]:
    r = S.compute({"ref": pair[0], "dist": pair[1]})
    check(0.0 <= r["ssim"] <= 1.0, "SSIM within [0,1] (%.5f)" % r["ssim"])

print("== T3 naive independent reference (both windows) ==")
for mode in ("gauss", "uniform"):
    max_mean_err = 0.0
    max_pix_err = 0.0
    for idx in range(5):  # naive O(N K^2): run on a 24x24 crop subset
        A = [row[:24] for row in PAIRS[idx][0][:24]]
        B = [row[:24] for row in PAIRS[idx][1][:24]]
        ref_map = naive_ssim_map(A, B, mode)
        got = S.compute({"ref": A, "dist": B, "window": mode})
        got_map = got["map"]
        check(len(ref_map) == len(got_map)
              and len(ref_map[0]) == len(got_map[0]),
              "%s pair%d map size matches" % (mode, idx))
        pe = max(abs(a - b) for ra, rb in zip(ref_map, got_map)
                 for a, b in zip(ra, rb))
        ref_mean = sum(sum(r) for r in ref_map) / (len(ref_map)
                                                   * len(ref_map[0]))
        me = abs(ref_mean - got["ssim"])
        max_mean_err = max(max_mean_err, me)
        max_pix_err = max(max_pix_err, pe)
    check(max_mean_err <= 0.005,
          "%s mean SSIM max error %.2e <= 0.005" % (mode, max_mean_err))
    check(max_pix_err <= 0.005,
          "%s per-pixel max error %.2e <= 0.005" % (mode, max_pix_err))

print("== T3b 20-image per-image SSIM vs naive reference ==")
for mode in ("gauss", "uniform"):
    worst = 0.0
    for idx, (ref, dist) in enumerate(PAIRS):
        ref_map = naive_ssim_map(ref, dist, mode)
        got = S.compute({"ref": ref, "dist": dist, "window": mode})
        ref_mean = (sum(sum(r) for r in ref_map)
                    / (len(ref_map) * len(ref_map[0])))
        err = abs(ref_mean - got["ssim"])
        worst = max(worst, err)
        check(err <= 0.005,
              "%s image %02d mean SSIM err %.2e <= 0.005" % (mode, idx, err))
    print("    %s: worst per-image error over 20 = %.2e" % (mode, worst))

print("== T4 block tiling bit-exactness (1 vs 2 vs 4) ==")
for mode in ("gauss", "uniform"):
    for scales in (1, 3):
        worst = 0.0
        for idx, (A, B) in enumerate(PAIRS):
            req = {"ref": A, "dist": B, "window": mode, "scales": scales}
            r1 = S.compute(dict(req, block=1))
            r2 = S.compute(dict(req, block=2))
            r4 = S.compute(dict(req, block=4))
            same2 = r1["map"] == r2["map"]
            same4 = r1["map"] == r4["map"]
            if not (same2 and same4):
                worst = max(worst,
                            max(abs(a - b) for ra, rb in zip(r1["map"], r4["map"])
                                for a, b in zip(ra, rb)))
            check(same2 and same4,
                  "%s scales=%d pair%d block 1/2/4 maps identical"
                  % (mode, scales, idx))
            check(r1["ssim"] == r2["ssim"] == r4["ssim"]
                  and r1["digest"] == r2["digest"] == r4["digest"],
                  "%s scales=%d pair%d ssim value+digest identical"
                  % (mode, scales, idx))
        check(worst == 0.0, "no floating drift across tiles (worst %s)" % worst)

print("== T5 multi-scale order and truncation ==")
A, B = PAIRS[0]
ms = S.compute({"ref": A, "dist": B, "window": "gauss", "scales": 3})
check(len(ms["per_scale"]) == 3, "per_scale has one entry per level")
# reconstruct level-2 inputs by applying the documented downsampling twice
a1, b1 = S.downsample2(A), S.downsample2(B)
a2, b2 = S.downsample2(a1), S.downsample2(b1)
last = S.compute({"ref": a2, "dist": b2, "window": "gauss", "scales": 1})
check(last["map"] == ms["map"],
      "last-scale map equals direct SSIM on 2x-downsampled inputs")
check(abs(last["ssim"] - ms["per_scale"][-1]) < 1e-12,
      "per_scale[-1] is the last-level mean")
# odd dimensions: 51x55 -> 25x27 -> 12x13 (odd remainder dropped each level);
# smallest level still fits the 11-tap gaussian window.
odd = make_ref(7, 51, 55)
odd_d = distort(odd, "gnoise", 7)
mo = S.compute({"ref": odd, "dist": odd_d, "scales": 3})
exp_h, exp_w = 51 // 4 - 11 + 1, 55 // 4 - 11 + 1
check(len(mo["map"]) == exp_h and len(mo["map"][0]) == exp_w,
      "odd sizes truncated before pooling (map %dx%d expected %dx%d)"
      % (len(mo["map"]), len(mo["map"][0]), exp_h, exp_w))
# undersized input is rejected explicitly instead of producing an empty map
small = make_ref(3, 30, 30)
try:
    S.compute({"ref": small, "dist": small, "scales": 3})
    check(False, "undersized 3-scale input should raise")
except ValueError:
    check(True, "undersized 3-scale input raises ValueError")
# CS at coarse levels uses C3. All checks below use the same `odd` instance,
# whose downsampled levels are o1/o2, so pipeline and formula see identical
# inputs.
k1d = S.kernel_for("gauss")
o1, p1 = S.downsample2(odd), S.downsample2(odd_d)
o2, p2 = S.downsample2(o1), S.downsample2(p1)
stats1 = S.local_stats(o1, p1, k1d)
cs_map = S.ssim_map_from_stats(stats1, last_scale=False)
ss_map = S.ssim_map_from_stats(stats1, last_scale=True)
check(cs_map != ss_map, "coarse-level CS map differs from SSIM map (C3 used)")
full3 = S.multiscale(odd, odd_d, "gauss", 3, 1)
check(abs(full3[2][1] - S.mean2(cs_map)) < 1e-12,
      "pipeline level-1 mean matches C3 CS formula on 2x-downsampled inputs")
# the smallest (last) level uses the full SSIM formula with C2
stats2 = S.local_stats(o2, p2, k1d)
ss_last = S.ssim_map_from_stats(stats2, last_scale=True)
check(abs(full3[2][2] - S.mean2(ss_last)) < 1e-12,
      "pipeline last-level mean matches full SSIM (C2) formula")

print("== T6 boundary convention: valid vs reflect-padded ==")
for mode in ("gauss", "uniform"):
    k = 11 if mode == "gauss" else 8
    r = k // 2
    Ap = reflect_pad(A, r)
    Bp = reflect_pad(B, r)
    padded = S.compute({"ref": Ap, "dist": Bp, "window": mode})["map"]
    valid = S.compute({"ref": A, "dist": B, "window": mode})["map"]
    # padded central region aligns exactly with the valid map
    ph, pw = len(padded), len(padded[0])
    inner = [row[r:r + len(valid[0])] for row in padded[r:r + len(valid)]]
    diff = max(abs(a - b) for ra, rb in zip(inner, valid)
               for a, b in zip(ra, rb))
    check(diff < 1e-12,
          "%s padded-map interior reproduces valid map exactly (%.1e)"
          % (mode, diff))
    # explainable boundary-only effect: full-frame means stay within 0.01
    pm = sum(sum(r) for r in padded) / (ph * pw)
    vm = sum(sum(r) for r in valid) / (len(valid) * len(valid[0]))
    check(abs(pm - vm) <= 0.01,
          "%s boundary convention shifts mean by %.4f <= 0.01"
          % (mode, abs(pm - vm)))

print("== T7 CLI contract and digest ==")
td = TMP
ip = os.path.join(td, "in.json")
op = os.path.join(td, "out.json")
with open(ip, "w") as f:
    json.dump({"ref": A, "dist": B, "window": "uniform",
               "scales": 1, "block": 2}, f)
subprocess.run([sys.executable, "ssim.py", ip, op], check=True)
out = json.load(open(op))
check(set(out) == {"ssim", "map", "per_scale", "digest"},
      "output keys match contract")
check(isinstance(out["ssim"], float) and isinstance(out["digest"], str)
      and len(out["digest"]) == 40, "ssim float and sha1 digest (40 hex)")
inproc = S.compute({"ref": A, "dist": B, "window": "uniform", "block": 2})
check(out["digest"] == inproc["digest"]
      and abs(out["ssim"] - inproc["ssim"]) < 1e-12,
      "CLI output matches in-process compute")

print("== T8 defective baseline regression ==")
# Run the original defective script as a real CLI subprocess for isolation.
td = TMP
ip = os.path.join(td, "base_in.json")
op = os.path.join(td, "base_out.json")
with open(ip, "w") as f:
    json.dump({"ref": [[int(round(v)) for v in row] for row in A],
               "dist": [[int(round(v)) for v in row] for row in B]}, f)
subprocess.run([sys.executable, "ssim_baseline_defective.py", ip, op],
               check=True)
base_val = json.load(open(op))["ssim"]
fixed = S.compute({"ref": A, "dist": B, "window": "uniform"})["ssim"]
check(abs(base_val - fixed) > 0.01,
      "baseline (stride4, C=1) differs from fixed SSIM: %.4f vs %.4f"
      % (base_val, fixed))

print()
if FAIL:
    print("RESULT: %d FAILURE(S)" % len(FAIL))
    for m in FAIL:
        print("  -", m)
    sys.exit(1)
print("RESULT: all checks passed")
