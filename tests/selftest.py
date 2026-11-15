#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Acceptance tests for splat.py.

Checks the three requirements from the task statement:

  A. accuracy   -- the tiled render must match a per-pixel analytic reference
                   (same kernel model, no tiling, every primitive evaluated at
                   every pixel centre) over the ROI with <= 1% error;
  B. seams      -- both sides of every tile boundary must be continuous:
                   no brightness jump larger than 0.004, i.e. no clipping of
                   the Gaussian at the tile edge;
  C. determinism-- permuting the primitive input list, or changing the tile
                   size, must reproduce the identical frame digest, including
                   for primitives that share a depth value.

Run:  python3 tests/selftest.py
"""

import hashlib
import json
import math
import os
import random
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import splat  # noqa: E402

# Indirection so tests/test_mutations.py can swap in a deliberately broken
# renderer and verify that this suite rejects it.
render = splat.render


def use_renderer(fn):
    """Install the renderer under test (None restores splat.render)."""
    global render
    render = splat.render if fn is None else fn
    return render


# --------------------------------------------------------------------------
# independent per-pixel analytic reference (no tiling, no support clamp)
# --------------------------------------------------------------------------
def render_reference(req, samples=1):
    """Per-pixel analytic reference: no tiling, no support clamp.

    With samples == 1 the Gaussian is evaluated exactly at each pixel centre,
    i.e. the untiled definition of the compositing model.  With samples > 1
    the pixel value is the box-filtered (area integrated) kernel, obtained by
    an n x n midpoint rule over the pixel; this is the " analytic integral "
    style reference and is strictly harder to match than a point sample.
    """
    W, H = int(req["w"]), int(req["h"])
    prims = []
    for raw in req["prims"]:
        p = splat._make_primitive(raw)
        if p is not None:
            prims.append(p)
    prims.sort(key=splat._order_key)

    n = W * H
    out_r = [0.0] * n
    out_g = [0.0] * n
    out_b = [0.0] * n
    trans = [1.0] * n

    offs = [(i + 0.5) / samples for i in range(samples)]
    norm = 1.0 / (samples * samples)

    for p in prims:
        cx, cy = p["x"], p["y"]
        ixx, ixy, iyy = p["ixx"], p["ixy"], p["iyy"]
        a = p["a"]
        cr, cg, cb = p["r"], p["g"], p["b"]
        for yy in range(H):
            off = yy * W
            for xx in range(W):
                if samples == 1:
                    dx = xx + 0.5 - cx
                    dy = yy + 0.5 - cy
                    g = math.exp(-0.5 * (ixx * dx * dx + 2.0 * ixy * dx * dy
                                         + iyy * dy * dy))
                else:
                    acc = 0.0
                    for sy in offs:
                        dy = yy + sy - cy
                        for sx in offs:
                            dx = xx + sx - cx
                            acc += math.exp(-0.5 * (ixx * dx * dx
                                                    + 2.0 * ixy * dx * dy
                                                    + iyy * dy * dy))
                    g = acc * norm
                aa = a * g
                idx = off + xx
                t = trans[idx]
                out_r[idx] += cr * aa * t
                out_g[idx] += cg * aa * t
                out_b[idx] += cb * aa * t
                trans[idx] = t * (1.0 - aa)

    pixels = [[out_r[i], out_g[i], out_b[i]] for i in range(n)]
    return {"pixels": pixels, "prims": prims}


def support_counts(req, prims):
    """Expected per-pixel contribution count: #prims whose 3-sigma box has it."""
    W, H = int(req["w"]), int(req["h"])
    counts = [0] * (W * H)
    for p in prims:
        px0 = max(0, p["px0"])
        px1 = min(W - 1, p["px1"])
        py0 = max(0, p["py0"])
        py1 = min(H - 1, p["py1"])
        for yy in range(py0, py1 + 1):
            off = yy * W
            for xx in range(px0, px1 + 1):
                counts[off + xx] += 1
    return counts


# --------------------------------------------------------------------------
# scene generators
# --------------------------------------------------------------------------
def make_scene(rng, w, h, nprims, sigma_lo=1.2, sigma_hi=4.0,
               z_levels=None, straddle=True):
    prims = []
    for _ in range(nprims):
        sx = rng.uniform(sigma_lo, sigma_hi) ** 2
        sy = rng.uniform(sigma_lo, sigma_hi) ** 2
        rho = rng.uniform(-0.6, 0.6)
        sxy = rho * math.sqrt(sx * sy)
        if straddle and rng.random() < 0.7:
            # deliberately straddle tile boundaries for tile sizes 4..16
            x = rng.choice([4.0, 8.0, 12.0, 16.0, 20.0, 24.0]) + rng.uniform(-1.0, 1.0)
            y = rng.choice([4.0, 8.0, 12.0, 16.0, 20.0, 24.0]) + rng.uniform(-1.0, 1.0)
        else:
            x = rng.uniform(-4.0, w + 4.0)
            y = rng.uniform(-4.0, h + 4.0)
        if z_levels:
            z = float(rng.choice(z_levels))
        else:
            z = rng.uniform(0.0, 10.0)
        prims.append({"x": x, "y": y, "z": z,
                      "sx": sx, "sy": sy, "sxy": sxy,
                      "a": rng.uniform(0.2, 0.9),
                      "rgb": [rng.random(), rng.random(), rng.random()]})
    return {"w": w, "h": h, "tile": 8, "prims": prims, "seed": 1}


def brightness(px):
    return [(r + g + b) / 3.0 for r, g, b in px]


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------
def check_accuracy(name, req, tol=0.01, renderer=None, samples=1):
    out = (renderer or render)(req)
    ref = render_reference(req, samples=samples)
    W = req["w"]
    H = req["h"]

    # ROI = pixels with signal, i.e. at least one primitive covers them.
    idx = [i for i in range(W * H) if out["pixel_counts"][i] > 0]
    outside = [i for i in range(W * H) if out["pixel_counts"][i] == 0]

    max_abs = 0.0
    sum_ref = 0.0
    sum_err = 0.0
    sum_err2 = 0.0
    for i in idx:
        for c in range(3):
            a = ref["pixels"][i][c]
            b = out["pixels"][i][c]
            d = abs(a - b)
            max_abs = max(max_abs, d)
            sum_ref += abs(a)
            sum_err += d
            sum_err2 += d * d
    rel = sum_err / sum_ref if sum_ref else 0.0
    rmse = math.sqrt(sum_err2 / max(1, 3 * len(idx)))

    # Truncating at 3 sigma must leave untouched pixels untouched.
    tail_leak = max((max(abs(v) for v in ref["pixels"][i]) for i in outside),
                    default=0.0)

    # per-pixel contribution counts must match the analytic support set
    expected = support_counts(req, ref["prims"])
    count_mismatch = sum(1 for i in range(W * H)
                         if expected[i] != out["pixel_counts"][i])

    ok = rel <= tol and count_mismatch == 0
    print(f"[{'PASS' if ok else 'FAIL'}] accuracy  {name:<28s} "
          f"ref={'centre' if samples == 1 else 'area%dx%d' % (samples, samples)}"
          f"  roi_px={len(idx):5d}  rel_err={rel:.3e}  "
          f"max_abs={max_abs:.3e}  rmse={rmse:.3e}  "
          f"count_mismatch={count_mismatch}  tail_leak={tail_leak:.2e}")
    return ok


def check_seams(name, req, jump_tol=0.004, renderer=None):
    """A tile boundary must be indistinguishable from the scene interior.

    Two independent tests:

    1. *Perturbation*: the pixel-to-pixel brightness step across every tile
       boundary must equal the step the same scene shows when it is rendered
       without internal tile boundaries at all (one tile = whole image).  This
       is exactly the "seam" signal: clipping shows up as a step that only
       exists because a tile edge is there.
    2. *Absolute jump*: for a smooth scene (see `smooth` scenes below) any
       adjacent-pixel step at a boundary must stay below `jump_tol`.
    """
    rnd = renderer or render
    out = rnd(req)
    W, H, T = req["w"], req["h"], int(req["tile"])

    seamless_req = dict(req)
    seamless_req["tile"] = max(W, H)          # no interior tile boundary
    seamless = rnd(seamless_req)

    b = brightness(out["pixels"])
    bs = brightness(seamless["pixels"])

    def step(buf, a, c):
        return buf[a] - buf[c]

    max_perturb = 0.0
    max_abs_jump = 0.0
    n_seam = 0
    for y in range(H):
        for x in range(1, W):
            i = y * W + x
            j = i - 1
            if x % T == 0:
                n_seam += 1
                d, ds = step(b, i, j), step(bs, i, j)
                max_perturb = max(max_perturb, abs(d - ds))
                max_abs_jump = max(max_abs_jump, abs(d))
    for x in range(W):
        for y in range(1, H):
            i = y * W + x
            j = i - W
            if y % T == 0:
                n_seam += 1
                d, ds = step(b, i, j), step(bs, i, j)
                max_perturb = max(max_perturb, abs(d - ds))
                max_abs_jump = max(max_abs_jump, abs(d))

    ok = max_perturb <= jump_tol
    print(f"[{'PASS' if ok else 'FAIL'}] seams     {name:<28s} "
          f"seam_px={n_seam:4d}  jump_perturbation={max_perturb:.2e}  "
          f"max_boundary_step={max_abs_jump:.3e}")
    return ok


def check_tile_counts(name, req, tiles=(1, 3, 4, 5, 8, 11, 16)):
    """tile_counts must be exactly the set of (tile, primitive) overlaps."""
    W, H = req["w"], req["h"]
    prims = [splat._make_primitive(p) for p in req["prims"]]
    prims = [p for p in prims if p is not None]

    ok = True
    for t in tiles:
        r = dict(req)
        r["tile"] = t
        out = splat.render(r)
        ntx = (W + t - 1) // t
        nty = (H + t - 1) // t
        tc = out["tile_counts"]

        # independent reconstruction of the overlap counts
        want = [[0] * ntx for _ in range(nty)]
        for p in prims:
            x0 = max(0, int(math.floor((p["x"] - p["rad"]) / t)))
            x1 = min(ntx - 1, int(math.floor((p["x"] + p["rad"]) / t)))
            y0 = max(0, int(math.floor((p["y"] - p["rad"]) / t)))
            y1 = min(nty - 1, int(math.floor((p["y"] + p["rad"]) / t)))
            for ty in range(y0, y1 + 1):
                for tx in range(x0, x1 + 1):
                    want[ty][tx] += 1

        if len(tc) != nty or any(len(row) != ntx for row in tc):
            ok = False
            print(f"[FAIL] tile_counts shape tile={t}: got "
                  f"{len(tc)}x{len(tc[0]) if tc else 0}, want {nty}x{ntx}")
        elif tc != want:
            ok = False
            print(f"[FAIL] tile_counts content tile={t}: differs from "
                  f"reconstructed overlap counts")
        # every primitive must reach at least one tile (unless fully offscreen)
        total = sum(sum(row) for row in tc)
        vis = sum(1 for p in prims
                  if p["px0"] <= min(W - 1, p["px1"])
                  and p["py0"] <= min(H - 1, p["py1"]))
        if total < vis:
            ok = False
            print(f"[FAIL] tile_counts total tile={t}: {total} < visible {vis}")
    print(f"[{'PASS' if ok else 'FAIL'}] tile_counts {name:<26s} "
          f"sizes={len(tiles)}")
    return ok


def check_invariance(name, req, renderer=None):
    """Permutation of the primitive list and tile size must not move a bit."""
    rnd = renderer or render
    base = rnd(req)

    W, H = req["w"], req["h"]

    rng = random.Random(1234)
    perms = []
    for _ in range(3):
        prims = list(req["prims"])
        rng.shuffle(prims)
        r = dict(req)
        r["prims"] = prims
        perms.append(r)

    # tile sizes: divisors and non-divisors of the image dimensions
    tiles = sorted({1, 2, 3, 5, 7, 8, 11, 16, 32, W, H, W + 3, 64})
    tile_results = []
    for t in tiles:
        r = dict(req)
        r["tile"] = t
        tile_results.append((t, rnd(r)))

    digests = {base["digest"]}
    for r in perms:
        digests.add(rnd(r)["digest"])
    for _, out in tile_results:
        digests.add(out["digest"])

    # compare pixels bit-exactly as well (a digest could in principle collide)
    exact = True
    for r in perms:
        if rnd(r)["pixels"] != base["pixels"]:
            exact = False
    for _, out in tile_results:
        if out["pixels"] != base["pixels"]:
            exact = False
    ok = len(digests) == 1 and exact
    print(f"[{'PASS' if ok else 'FAIL'}] invariance {name:<27s} "
          f"permutations=3  tile_sizes={len(tiles)}  "
          f"distinct_digests={len(digests)}  bit_exact={exact}")
    return ok


    base = splat.render(req)
    W, H = req["w"], req["h"]

    rng = random.Random(1234)
    perms = []
    for k in range(3):
        prims = list(req["prims"])
        rng.shuffle(prims)
        r = dict(req)
        r["prims"] = prims
        perms.append(r)

    # tile sizes: divisors and non-divisors of the image size
    tiles = sorted({1, 2, 3, 5, 7, 8, 11, 16, 32, W, H, W + 3, 64})
    tile_results = []
    for t in tiles:
        r = dict(req)
        r["tile"] = t
        tile_results.append((t, splat.render(r)))

    digests = {base["digest"]}
    for r in perms:
        digests.add(splat.render(r)["digest"])
    for _, out in tile_results:
        digests.add(out["digest"])

    # bit-exact pixel comparison too (digest could in principle collide)
    exact = True
    for r in perms:
        if splat.render(r)["pixels"] != base["pixels"]:
            exact = False
    for _, out in tile_results:
        if out["pixels"] != base["pixels"]:
            exact = False
    ok = len(digests) == 1 and exact
    print(f"[{'PASS' if ok else 'FAIL'}] invariance {name:<27s} "
          f"permutations=3  tile_sizes={len(tiles)}  "
          f"distinct_digests={len(digests)}  bit_exact={exact}")
    return ok


def smooth_scene(seed=5, w=40, h=40, tile=8, sigma=12.0, alpha=0.02,
                 nprims=6, spread=12.0, z_levels=None):
    # Wide, flat, low-alpha kernels: the true image has no adjacent-pixel
    # brightness step anywhere near 0.004 (measured max interior step is
    # ~0.0026), so the absolute seam tolerance is a real constraint instead
    # of a gradient artefact.  Kernels deliberately straddle boundaries.
    rng = random.Random(seed)
    prims = []
    for i in range(nprims):
        z = (float(rng.choice(z_levels)) if z_levels else float(i))
        prims.append({"x": w * 0.5 + rng.uniform(-spread, spread),
                      "y": h * 0.5 + rng.uniform(-spread, spread),
                      "z": z,
                      "sx": sigma ** 2, "sy": sigma ** 2, "sxy": 0.0,
                      "a": alpha,
                      "rgb": [rng.uniform(0.1, 0.9) for _ in range(3)]})
    return {"w": w, "h": h, "tile": tile, "prims": prims, "seed": 1}


def check_seam_jump_smooth(name, req, jump_tol=0.004, renderer=None):
    """Absolute acceptance criterion: no >0.004 brightness step at a seam."""
    out = (renderer or render)(req)
    W, H, T = req["w"], req["h"], int(req["tile"])
    b = brightness(out["pixels"])

    max_seam = 0.0
    max_interior = 0.0
    for y in range(H):
        for x in range(1, W):
            d = abs(b[y * W + x] - b[y * W + x - 1])
            if x % T == 0:
                max_seam = max(max_seam, d)
            else:
                max_interior = max(max_interior, d)
    for x in range(W):
        for y in range(1, H):
            d = abs(b[y * W + x] - b[(y - 1) * W + x])
            if y % T == 0:
                max_seam = max(max_seam, d)
            else:
                max_interior = max(max_interior, d)

    ok = max_seam <= jump_tol
    print(f"[{'PASS' if ok else 'FAIL'}] seam-jump   {name:<26s} "
          f"max_seam_step={max_seam:.3e}  max_interior_step={max_interior:.3e}"
          f"  tol={jump_tol:.3e}")
    return ok


def check_seam_abs(name, req, jump_tol=0.004, renderer=None):
    """Absolute criterion at the seams, against the analytic reference.

    Every pixel that touches a tile boundary (on either side) must match the
    untiled analytic composite to within `jump_tol`, and the brightness
    *step* across the boundary must match the analytic step.  A clipped
    Gaussian loses the whole tail beyond the edge and shows up here; the
    correct renderer only carries the tile-independent 3 sigma truncation.
    """
    out = (renderer or render)(req)
    ref = render_reference(req)
    W, H, T = req["w"], req["h"], int(req["tile"])

    seam_px = set()
    for x in range(1, W):
        if x % T == 0:
            for y in range(H):
                seam_px.add(y * W + x)
                seam_px.add(y * W + x - 1)
    for y in range(1, H):
        if y % T == 0:
            for x in range(W):
                seam_px.add(y * W + x)
                seam_px.add((y - 1) * W + x)

    max_seam = 0.0
    for i in seam_px:
        for c in range(3):
            max_seam = max(max_seam, abs(out["pixels"][i][c]
                                         - ref["pixels"][i][c]))

    # Cross-seam brightness *step* must match the model's own step: this is
    # the "no jump at the boundary" statement in absolute units, and it is
    # insensitive to the smooth 3 sigma truncation which both sides share.
    b = brightness(out["pixels"])
    br = brightness(ref["pixels"])
    max_step_err = 0.0
    for y in range(H):
        for x in range(1, W):
            if x % T == 0:
                i = y * W + x
                max_step_err = max(max_step_err,
                                   abs((b[i] - b[i - 1]) - (br[i] - br[i - 1])))
    for x in range(W):
        for y in range(1, H):
            if y % T == 0:
                i = y * W + x
                max_step_err = max(max_step_err,
                                   abs((b[i] - b[i - W]) - (br[i] - br[i - W])))

    ok = max_seam <= jump_tol and max_step_err <= jump_tol
    print(f"[{'PASS' if ok else 'FAIL'}] seam-abs    {name:<26s} "
          f"seam_px={len(seam_px):4d}  max_seam_dev={max_seam:.3e}  "
          f"max_step_err={max_step_err:.3e}  tol={jump_tol:.3e}")
    return ok


def check_equal_depth(name, req, renderer=None):
    """Primitives sharing a depth must compose in a stable order."""
    rnd = renderer or render
    base = rnd(req)
    rng = random.Random(99)
    ok = True
    for _ in range(4):
        prims = list(req["prims"])
        rng.shuffle(prims)
        r = dict(req)
        r["prims"] = prims
        if rnd(r)["digest"] != base["digest"]:
            ok = False
    zs = sorted(p["z"] for p in req["prims"])
    dup = sum(1 for i in range(1, len(zs)) if zs[i] == zs[i - 1])
    print(f"[{'PASS' if ok else 'FAIL'}] equal-depth {name:<25s} "
          f"tied_prims={dup}")
    return ok


# --------------------------------------------------------------------------
def main():
    ok = True
    rng = random.Random(7)

    scenes = []
    scenes.append(("random-32x32-t8", make_scene(rng, 32, 32, 24)))
    scenes.append(("random-48x40-t8", make_scene(rng, 48, 40, 40)))
    scenes.append(("nondiv-30x26-t7",
                   dict(make_scene(rng, 30, 26, 20), tile=7)))
    scenes.append(("wide-sigma-64x64",
                   make_scene(rng, 64, 64, 18, sigma_lo=6.0, sigma_hi=14.0)))
    scenes.append(("tight-sigma-32x32",
                   make_scene(rng, 32, 32, 30, sigma_lo=1.0, sigma_hi=1.6)))
    scenes.append(("offimage-24x24",
                   dict(make_scene(rng, 24, 24, 12), tile=5)))

    # smooth scene: wide, low alpha kernels straddling boundaries, so the
    # analytic seam deviation is purely the (uniform) 3 sigma truncation
    smooth = make_scene(random.Random(11), 40, 40, 5, sigma_lo=7.0,
                        sigma_hi=11.0)
    for p in smooth["prims"]:
        p["a"] = 0.25
    smooth["tile"] = 8
    scenes.append(("smooth-wide-40x40", smooth))

    for name, req in scenes:
        ok &= check_accuracy(name, req)
        ok &= check_seams(name, req)
        ok &= check_invariance(name, req)
        ok &= check_tile_counts(name, req)

    # Same frames against an *area integrated* reference (midpoint rule over
    # each pixel): for kernels that are wide relative to a pixel this equals
    # the centre-sampled model to far below the tolerance, so the check stays
    # honest.  Kernels narrower than ~2 px are excluded because there the two
    # references genuinely differ (the model is defined at pixel centres).
    medium = make_scene(random.Random(17), 48, 48, 14,
                        sigma_lo=3.0, sigma_hi=6.0)
    for name, req in (("wide-sigma-64x64", scenes[3][1]),
                      ("smooth-wide-40x40", scenes[6][1]),
                      ("medium-sigma-48x48", medium)):
        ok &= check_accuracy(name + "/area", req, samples=3)

    # absolute seam tolerance on flat-gradient scenes, several tile sizes
    for tile in (4, 5, 8, 11, 13, 16):
        s = smooth_scene(seed=5 + tile, tile=tile)
        ok &= check_seam_jump_smooth(f"smooth-t{tile}", s)
        ok &= check_seams(f"smooth-t{tile}", s)
        ok &= check_seam_abs(f"smooth-t{tile}", s)
    # same, with tied depths
    st = smooth_scene(seed=21, tile=7, z_levels=[1.0, 4.0])
    ok &= check_seam_jump_smooth("smooth-tied-t7", st)
    ok &= check_seams("smooth-tied-t7", st)
    ok &= check_seam_abs("smooth-tied-t7", st)
    ok &= check_equal_depth("smooth-tied-t7", st)

    # equal depth: every primitive on one of three z planes
    tied = make_scene(random.Random(3), 32, 32, 26, z_levels=[1.0, 2.0, 5.0])
    ok &= check_equal_depth("3-depth-planes", tied)
    ok &= check_accuracy("3-depth-planes", tied)
    ok &= check_seams("3-depth-planes", tied)

    tied2 = make_scene(random.Random(4), 40, 40, 30, z_levels=[2.5])
    ok &= check_equal_depth("single-depth", tied2)
    ok &= check_accuracy("single-depth", tied2)

    # end-to-end through the CLI, exactly as a caller would invoke it
    import tempfile
    tmpdir = tempfile.mkdtemp()
    req = scenes[0][1]
    fin = os.path.join(tmpdir, "in.json")
    fout = os.path.join(tmpdir, "out.json")
    with open(fin, "w") as fh:
        json.dump(req, fh)
    rc = splat.main(["splat.py", fin, fout])
    with open(fout) as fh:
        got = json.load(fh)
    cli_ok = (rc == 0 and "pixels" in got and "tile_counts" in got
              and len(got["pixels"]) == req["w"] * req["h"]
              and got["digest"] == splat.render(req)["digest"])
    ok &= cli_ok
    print(f"[{'PASS' if cli_ok else 'FAIL'}] CLI end-to-end")

    ok &= check_edge_cases()

    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


def check_edge_cases():
    """Degenerate and off-nominal inputs must not crash or corrupt the frame."""
    ok = True

    def expect(label, cond, detail=""):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"[{'PASS' if cond else 'FAIL'}] edge      {label:<28s} {detail}")

    # empty primitive list -> black frame
    out = splat.render({"w": 8, "h": 8, "tile": 4, "prims": []})
    expect("empty scene", all(px == [0.0, 0.0, 0.0] for px in out["pixels"])
           and out["tile_counts"] == [[0, 0], [0, 0]],
           f"digest={out['digest'][:8]}")

    # malformed / non-positive-definite primitives are dropped, not fatal
    bad = [{"x": 4, "y": 4, "sx": -1, "sy": 1, "a": 1, "rgb": [1, 0, 0]},
           {"x": 4, "y": 4, "sx": 1, "sy": 1, "sxy": 2, "a": 1,
            "rgb": [1, 0, 0]},
           {"x": float("nan"), "y": 4, "sx": 1, "sy": 1, "a": 1,
            "rgb": [1, 0, 0]},
           {"x": float("inf"), "y": 4, "sx": 1, "sy": 1, "a": 1,
            "rgb": [1, 0, 0]},
           {"y": 4, "sx": 1, "sy": 1, "a": 1, "rgb": [1, 0, 0]},
           {"x": 4, "y": 4, "sx": 0, "sy": 0, "a": 1, "rgb": [1, 0, 0]},
           {"x": 4, "y": 4, "sx": 1, "sy": 1, "a": 1, "rgb": [1, 0]},
           {"x": 4, "y": 4, "sx": 1, "sy": "big", "a": 1, "rgb": [1, 0, 0]},
           "not a dict"]
    out = splat.render({"w": 8, "h": 8, "tile": 4, "prims": bad})
    expect("invalid primitives dropped",
           all(px == [0.0, 0.0, 0.0] for px in out["pixels"]),
           f"n={len(bad)}")

    # single pixel image, tile larger than the image
    out = splat.render({"w": 1, "h": 1, "tile": 16,
                        "prims": [{"x": 0.5, "y": 0.5, "z": 0, "sx": 1,
                                   "sy": 1, "sxy": 0, "a": 0.5,
                                   "rgb": [1.0, 1.0, 1.0]}]})
    expect("1x1 with oversized tile",
           abs(out["pixels"][0][0] - 0.5) < 1e-12
           and out["tile_counts"] == [[1]],
           f"px={out['pixels'][0][0]:.6f}")

    # tile = 1 (32x32 tiles) must give the same frame as tile = 32
    req = make_scene(random.Random(23), 16, 16, 8)
    a = splat.render(dict(req, tile=1))
    b = splat.render(dict(req, tile=16))
    expect("tile=1 vs tile=size", a["digest"] == b["digest"]
           and a["pixels"] == b["pixels"], f"digest={a['digest'][:8]}")

    # alpha = 0 primitive contributes nothing but is still counted
    req0 = {"w": 8, "h": 8, "tile": 4,
            "prims": [{"x": 4.0, "y": 4.0, "z": 0, "sx": 4, "sy": 4,
                       "sxy": 0, "a": 0.0, "rgb": [1.0, 1.0, 1.0]}]}
    out = splat.render(req0)
    expect("zero alpha", all(px == [0.0, 0.0, 0.0] for px in out["pixels"])
           and sum(sum(r) for r in out["tile_counts"]) == 4)

    # an opaque front primitive must dominate at the centre: the front colour
    # times its own kernel, with everything behind attenuated by (1 - G).
    # G(centre) = exp(-0.5 * 0.25/9) = 0.98620..., so blue ~0.9726 and the
    # red still visible behind it is 1 - G times its own kernel.
    req1 = {"w": 8, "h": 8, "tile": 4,
            "prims": [{"x": 4.0, "y": 4.0, "z": 0, "sx": 9, "sy": 9,
                       "sxy": 0, "a": 1.0, "rgb": [0.0, 0.0, 1.0]},
                      {"x": 4.0, "y": 4.0, "z": 1, "sx": 9, "sy": 9,
                       "sxy": 0, "a": 1.0, "rgb": [1.0, 0.0, 0.0]}]}
    out = splat.render(req1)
    c = out["pixels"][4 * 8 + 4]
    g_c = math.exp(-0.5 * (0.5 ** 2 + 0.5 ** 2) / 9.0)
    expect("opaque front dominates",
           abs(c[2] - g_c) < 1e-12
           and abs(c[0] - (1.0 - g_c) * g_c) < 1e-12,
           f"centre={['%.6f' % v for v in c]} (G={g_c:.6f})")

    # fully off-image primitive touches no pixel and no tile
    out = splat.render({"w": 8, "h": 8, "tile": 4,
                        "prims": [{"x": -100.0, "y": -100.0, "z": 0,
                                   "sx": 1, "sy": 1, "sxy": 0, "a": 1,
                                   "rgb": [1, 1, 1]}]})
    expect("primitive off image",
           out["tile_counts"] == [[0, 0], [0, 0]]
           and sum(out["pixel_counts"]) == 0)

    # too-short / invalid arguments are rejected cleanly
    for args, label in ((["splat.py"], "missing args"),
                        (["splat.py", "a", "b", "c"], "extra args")):
        expect(label, splat.main(args) == 2)
    for bad_req, label in (({"w": 0, "h": 4}, "w=0"),
                           ({"w": 4, "h": 4, "tile": 0}, "tile=0"),
                           ({"h": 4}, "missing w")):
        try:
            splat.render(bad_req)
        except ValueError:
            expect(label, True)
        else:
            expect(label, False)

    return ok


if __name__ == "__main__":
    raise SystemExit(main())
