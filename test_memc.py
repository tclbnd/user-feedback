#!/usr/bin/env python3
"""Acceptance tests for occlusion-aware MEMC frame interpolation.

Covers:
  1. Synthetic known motion (translation, scaling, occlusion crossing):
     interpolated frame vs ground truth PSNR >= 30 dB, ghost-pixel ratio
     inside the occlusion region <= 1%.
  2. Low-texture / repeated-texture mismatch suppression.
  3. Block-order invariance: shuffling the block matching order leaves the
     occlusion map and the interpolation summary bit-identical.
  4. Object leaving the frame: no foreground/background mixing.
"""
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memc

W, H = 96, 64
T = 0.5
PASS = []


def report(name, ok, detail=""):
    PASS.append((name, ok))
    print("%s %-38s %s" % ("PASS" if ok else "FAIL", name, detail))


def psnr(a, b):
    h = len(a)
    w = len(a[0])
    se = 0.0
    for y in range(h):
        for x in range(w):
            d = a[y][x] - b[y][x]
            se += d * d
    mse = se / (w * h)
    if mse == 0:
        return 99.0
    return 10.0 * math.log10(255.0 * 255.0 / mse)


# ------------------------------------------------------------ scenes
def scene_translate(w=W, h=H, dx=10, seed=0):
    rng = random.Random(seed)
    bg = [[rng.randrange(40, 80) for _ in range(w)] for _ in range(h)]

    def frame(shift):
        f = [r[:] for r in bg]
        s = int(round(shift))
        for y in range(12):
            for x in range(12):
                f[10 + y][20 + s + x] = 220
        return f

    return frame(0), frame(dx), frame(dx * T)


def scene_occlusion(w=W, h=H, dx=16, seed=1):
    rng = random.Random(seed)
    bg = [[rng.randrange(30, 60) for _ in range(w)] for _ in range(h)]

    def frame(shift):
        f = [r[:] for r in bg]
        s = int(round(shift))
        for y in range(8, 24):
            for x in range(8 + s, 24 + s):
                f[y][x] = 200
        return f

    return frame(0), frame(dx), frame(dx * T)


def scene_scaling(w=W, h=H, s1=1.05, seed=5):
    """Zoom about the image centre; textured (matchable) content."""
    rng = random.Random(seed)
    raw = [[rng.randrange(0, 256) for _ in range(w)] for _ in range(h)]
    # light smoothing so sub-pixel resampling stays well behaved
    base = [[(raw[y][x] + raw[y][min(x + 1, w - 1)]
              + raw[min(y + 1, h - 1)][x]
              + raw[min(y + 1, h - 1)][min(x + 1, w - 1)] + 2) // 4
             for x in range(w)] for y in range(h)]
    cx, cy = w / 2.0, h / 2.0

    def frame(s):
        img = []
        for y in range(h):
            row = []
            for x in range(w):
                bx = (x - cx) / s + cx
                by = (y - cy) / s + cy
                row.append(int(round(memc.sample_bilinear(base, bx, by))))
            img.append(row)
        return img

    smid = 1.0 + (s1 - 1.0) * T
    return frame(1.0), frame(s1), frame(smid)


def scene_lowtex(w=W, h=H, seed=7):
    rng = random.Random(seed)
    f0 = [[100 + (x // 24) + rng.randrange(0, 2) for x in range(w)]
          for _ in range(h)]
    f1 = [r[:] for r in f0]
    gt = [r[:] for r in f0]
    return f0, f1, gt


def scene_repeated(w=W, h=H, shift=3):
    def frame(s):
        return [[max(0, min(255, int(round(
            128 + 100 * math.sin((x - s) * math.pi / 4.0)))))
            for x in range(w)] for _ in range(h)]

    return frame(0), frame(shift), frame(shift * T)


def scene_leave(w=W, h=H, x0=80, dx=12, seed=3):
    rng = random.Random(seed)
    bg = [[rng.randrange(40, 80) for _ in range(w)] for _ in range(h)]

    def frame(shift):
        f = [r[:] for r in bg]
        s = int(round(shift))
        for y in range(12):
            for x in range(12):
                xx = x0 + s + x
                if 0 <= xx < w:
                    f[10 + y][xx] = 220
        return f

    return frame(0), frame(dx), frame(dx * T)


# ------------------------------------------------------------ checks
def check_psnr(name, f0, f1, gt, thresh=30.0):
    res = memc.interpolate_pair(f0, f1, T, block=8, radius=8, levels=2)
    p = psnr(res["img"], gt)
    report(name + " PSNR>=30dB", p >= thresh, "psnr=%.2f" % p)
    return res


def check_ghost(name, res, gt, region, thresh=0.01):
    """Ghost = interpolated value matches neither plausible source level."""
    (x0, y0, x1, y1) = region
    total = 0
    ghost = 0
    img = res["img"]
    for y in range(y0, y1):
        for x in range(x0, x1):
            total += 1
            if abs(img[y][x] - gt[y][x]) > 40:
                ghost += 1
    ratio = ghost / float(total)
    report(name + " ghost<=1%", ratio <= thresh,
           "ghost=%.4f (%d/%d)" % (ratio, ghost, total))
    return ratio


def shuffled_order(nbx, nby, seed):
    order = list(range(nbx * nby))
    random.Random(seed).shuffle(order)
    return order


def check_order_invariance(f0, f1):
    base = memc.interpolate_pair(f0, f1, T, block=8, radius=8, levels=2)
    ok = True
    for seed in (11, 22, 33):
        order = shuffled_order(12, 8, seed)
        res = memc.interpolate_pair(f0, f1, T, block=8, radius=8, levels=2,
                                    order=order)
        if res["occl"] != base["occl"]:
            ok = False
            print("  occl map differs for seed", seed)
        if res["img"] != base["img"]:
            ok = False
            print("  interp image differs for seed", seed)
        if res["stats"] != base["stats"]:
            ok = False
            print("  stats differ for seed", seed)
    report("block-order invariance", ok)


def check_end_to_end():
    """CLI: request.json -> interp/occl/mv/stats outputs."""
    d = tempfile.mkdtemp()
    try:
        f0, f1, _ = scene_translate()
        memc.write_pgm(os.path.join(d, "f0.pgm"), W, H, f0)
        memc.write_pgm(os.path.join(d, "f1.pgm"), W, H, f1)
        req = {"frames": [{"file": "f0.pgm"}, {"file": "f1.pgm"}],
               "t": [0.5], "block": 8}
        with open(os.path.join(d, "request.json"), "w") as f:
            json.dump(req, f)
        out = os.path.join(d, "out")
        subprocess.check_call([sys.executable,
                               os.path.join(os.path.dirname(
                                   os.path.abspath(__file__)), "memc.py"),
                               os.path.join(d, "request.json"), out])
        ok = True
        for fn in ("interp_0.5.pgm", "occl_0.5.pgm", "mv_0.5.json",
                   "stats.json"):
            if not os.path.exists(os.path.join(out, fn)):
                ok = False
                print("  missing output", fn)
        stats = json.load(open(os.path.join(out, "stats.json")))
        for key in ("occl_ratio", "fallback_px", "mv_count"):
            if key not in stats:
                ok = False
                print("  missing stats key", key)
        if stats.get("mv_count", 0) <= 0:
            ok = False
            print("  mv_count not positive")
        report("CLI end-to-end outputs", ok)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def run_cli(d, req, out):
    rq = os.path.join(d, "request.json")
    with open(rq, "w") as f:
        json.dump(req, f)
    subprocess.check_call([sys.executable,
                           os.path.join(os.path.dirname(
                               os.path.abspath(__file__)), "memc.py"),
                           rq, out])
    return out


def check_multi_t_and_pairs():
    """Multiple t values and multiple frame pairs in one request."""
    d = tempfile.mkdtemp()
    try:
        f0, f1, gt = scene_translate()
        g0, g1, ggt = scene_occlusion()
        for name, img in (("f0.pgm", f0), ("f1.pgm", f1),
                          ("g0.pgm", g0), ("g1.pgm", g1)):
            memc.write_pgm(os.path.join(d, name), W, H, img)
        req = {"frames": [{"file": "f0.pgm"}, {"file": "f1.pgm"}],
               "t": [0.25, 0.5, 0.75], "block": 8}
        out = run_cli(d, req, os.path.join(d, "out"))
        ok = all(os.path.exists(os.path.join(out, "interp_%s.pgm" % t))
                 for t in ("0.25", "0.5", "0.75"))
        # intermediate frames must be ordered: 0.25 closer to f0 than 0.75
        def load(p):
            _, _, img = memc.read_pgm(p)
            return img
        i25 = load(os.path.join(out, "interp_0.25.pgm"))
        i75 = load(os.path.join(out, "interp_0.75.pgm"))
        d25 = sum(abs(a - b) for r0, r1 in zip(i25, f0)
                  for a, b in zip(r0, r1))
        d75 = sum(abs(a - b) for r0, r1 in zip(i75, f0)
                  for a, b in zip(r0, r1))
        ok = ok and d25 < d75
        # multi-pair: 3 frames -> two pairs, indexed output names
        req = {"frames": [{"file": "f0.pgm"}, {"file": "f1.pgm"},
                          {"file": "g1.pgm"}],
               "t": [0.5], "block": 8}
        out2 = run_cli(d, req, os.path.join(d, "out2"))
        ok = ok and os.path.exists(os.path.join(out2, "interp_0_0.5.pgm"))
        ok = ok and os.path.exists(os.path.join(out2, "interp_1_0.5.pgm"))
        report("multi-t and multi-pair", ok)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def check_cli_determinism():
    """Two identical CLI runs must produce byte-identical outputs."""
    d = tempfile.mkdtemp()
    try:
        f0, f1, _ = scene_occlusion()
        memc.write_pgm(os.path.join(d, "f0.pgm"), W, H, f0)
        memc.write_pgm(os.path.join(d, "f1.pgm"), W, H, f1)
        req = {"frames": [{"file": "f0.pgm"}, {"file": "f1.pgm"}],
               "t": [0.5], "block": 8}
        o1 = run_cli(d, req, os.path.join(d, "o1"))
        o2 = run_cli(d, req, os.path.join(d, "o2"))
        ok = True
        for fn in ("interp_0.5.pgm", "occl_0.5.pgm", "mv_0.5.json",
                   "stats.json"):
            a = open(os.path.join(o1, fn), "rb").read()
            b = open(os.path.join(o2, fn), "rb").read()
            if a != b:
                ok = False
                print("  nondeterministic output", fn)
        report("CLI determinism", ok)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def check_fixture_scenes():
    """The repo's own make_scene.py scenes through the CLI."""
    d = tempfile.mkdtemp()
    try:
        subprocess.check_call([sys.executable,
                               os.path.join(os.path.dirname(
                                   os.path.abspath(__file__)),
                                   "make_scene.py"), d])
        req = {"frames": [{"file": "f0.pgm"}, {"file": "f1.pgm"}],
               "t": [0.5], "block": 8}
        out = run_cli(d, req, os.path.join(d, "out"))
        _, _, img = memc.read_pgm(os.path.join(out, "interp_0.5.pgm"))
        _, _, gt = scene_translate()      # same generator, same seed
        p = psnr(img, gt)
        req = {"frames": [{"file": "g0.pgm"}, {"file": "g1.pgm"}],
               "t": [0.5], "block": 8}
        out = run_cli(d, req, os.path.join(d, "outg"))
        _, _, imgg = memc.read_pgm(os.path.join(out, "interp_0.5.pgm"))
        _, _, gtg = scene_occlusion()
        pg = psnr(imgg, gtg)
        report("fixture scenes PSNR>=30dB", p >= 30.0 and pg >= 30.0,
               "translate=%.2f occlusion=%.2f" % (p, pg))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    # 1. known synthetic motion: PSNR vs ground truth
    f0, f1, gt = scene_translate()
    res_t = check_psnr("translate", f0, f1, gt)

    f0, f1, gt = scene_scaling()
    check_psnr("scaling", f0, f1, gt)

    f0, f1, gt = scene_occlusion()
    res_o = check_psnr("occlusion-crossing", f0, f1, gt)
    # occlusion region: swept area of the moving box
    check_ghost("occlusion-crossing", res_o, gt, (8, 8, 40, 24))

    # 2. low-texture / repeated-texture mismatch suppression
    f0, f1, gt = scene_lowtex()
    res_l = memc.interpolate_pair(f0, f1, T, block=8, radius=8, levels=2)
    p = psnr(res_l["img"], gt)
    nz = sum(1 for row in res_l["f01"][0] for v in row if v != 0.0)
    report("low-texture PSNR>=30dB", p >= 30.0, "psnr=%.2f" % p)
    report("low-texture zero-motion", nz == 0,
           "nonzero mvs=%d occl=%.4f" % (nz, res_l["stats"]["occl_ratio"]))

    f0, f1, gt = scene_repeated()
    res_r = check_psnr("repeated-texture", f0, f1, gt)
    mvs = res_r["f01"][0]
    bad = sum(1 for row in mvs for v in row if v not in (0.0, 3.0))
    report("repeated-texture mv sanity", bad == 0, "bad mvs=%d" % bad)

    # 3. block-order invariance (occlusion scene, hardest case)
    f0, f1, _ = scene_occlusion()
    check_order_invariance(f0, f1)

    # 4. object leaving the frame: no fg/bg mixing (values must stay
    #    close to one of the two source frames, never a blend)
    f0, f1, gt = scene_leave()
    res_v = memc.interpolate_pair(f0, f1, T, block=8, radius=8, levels=2)
    mixed = 0
    for y in range(H):
        for x in range(W):
            v = res_v["img"][y][x]
            if min(abs(v - f0[y][x]), abs(v - f1[y][x])) > 24:
                mixed += 1
    ratio = mixed / float(W * H)
    report("leave-frame no-mixing", ratio <= 0.01,
           "mixed=%.4f (%d)" % (ratio, mixed))

    # 5. CLI contract
    check_end_to_end()
    check_multi_t_and_pairs()
    check_cli_determinism()
    check_fixture_scenes()

    n_fail = sum(1 for _, ok in PASS if not ok)
    print("\n%d/%d checks passed" % (len(PASS) - n_fail, len(PASS)))
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
