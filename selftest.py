#!/usr/bin/env python3
"""自测: 合成场景生成 + PSNR/重影/顺序无关性验证。

用法: python3 selftest.py
所有临时产物写入 $TMPDIR/memc_selftest, 不污染源码目录。
"""
import json
import math
import os
import random
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memc  # noqa: E402

W, H = 96, 64


# ---------------- 场景: 全部带 t 时刻真值与目标掩码 ----------------
def scene_translate(dx=10):
    rng = random.Random(0)
    bg = [[rng.randrange(40, 80) for _ in range(W)] for _ in range(H)]

    def frame(x0):
        f = [r[:] for r in bg]
        for y in range(10, 22):
            for x in range(x0, x0 + 12):
                if 0 <= x < W:
                    f[y][x] = 220
        return f

    def gt(t):
        return frame(20 + round(dx * t))

    def mask(t):
        x0 = 20 + round(dx * t)
        return {(y, x) for y in range(10, 22) for x in range(x0, x0 + 12)}

    return frame(20), frame(20 + dx), gt, mask


def scene_occlusion():
    rng = random.Random(1)
    bg = [[rng.randrange(30, 60) for _ in range(W)] for _ in range(H)]

    def frame(x0):
        f = [r[:] for r in bg]
        for y in range(8, 24):
            for x in range(x0, x0 + 16):
                if 0 <= x < W:
                    f[y][x] = 200
        return f

    def gt(t):
        return frame(round(8 + 16 * t))

    def mask(t):  # 运动目标在 t 时刻占据区 (含两侧遮挡/显露带)
        x0 = round(8 + 16 * t)
        return {(y, x) for y in range(8, 24) for x in range(x0, x0 + 16)}

    return frame(8), frame(24), gt, mask


def scene_zoom():
    rng = random.Random(2)
    bg = [[rng.randrange(40, 90) for _ in range(W)] for _ in range(H)]
    # 目标规范图案 32x32: 棋盘 + 亮边框, 保证块匹配有纹理
    pat = [[0] * 32 for _ in range(32)]
    for y in range(32):
        for x in range(32):
            edge = x in (0, 31) or y in (0, 31)
            pat[y][x] = 230 if edge else (170 if ((x // 4) + (y // 4)) % 2 else 90)
    cx, cy, s0, s1 = 48, 32, 1.0, 1.3

    def frame(scale):
        f = [r[:] for r in bg]
        half = 16 * scale
        for y in range(H):
            for x in range(W):
                nx = (x - cx) / scale
                ny = (y - cy) / scale
                if -16 <= nx < 16 and -16 <= ny < 16:
                    f[y][x] = pat[max(0, min(31, int(ny + 16)))][
                        max(0, min(31, int(nx + 16)))]
        return f, half

    def gt(t):
        return frame(s0 + (s1 - s0) * t)[0]

    def mask(t):
        sc = s0 + (s1 - s0) * t
        half = 16 * sc
        return {(y, x) for y in range(H) for x in range(W)
                if abs(x - cx) <= half and abs(y - cy) <= half}

    f0, _ = frame(s0)
    f1, _ = frame(s1)
    return f0, f1, gt, mask


def scene_low_texture():
    rng = random.Random(3)
    bg = [[100 + rng.randrange(-1, 2) for _ in range(W)] for _ in range(H)]

    def frame(x0):
        f = [r[:] for r in bg]
        for y in range(14, 30):
            for x in range(x0, x0 + 16):
                if 0 <= x < W:
                    f[y][x] = 220
        return f

    def gt(t):
        return frame(round(18 + 8 * t))

    def mask(t):
        x0 = round(18 + 8 * t)
        return {(y, x) for y in range(14, 30) for x in range(x0, x0 + 16)}

    return frame(18), frame(26), gt, mask


def scene_repeated():
    """周期条纹背景 + 带纹理目标; 左侧放随机噪声锚区提供可靠种子。"""
    rng = random.Random(4)
    bg = [[0] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            bg[y][x] = rng.randrange(40, 90) if x < 20 else (130 if (x // 3) % 2 else 70)
    # 目标内部加固定图案, 避免纯色
    box = [[(210 if (x + y) % 2 == 0 else 180) for x in range(14)] for y in range(14)]
    dx = 6

    def frame(x0):
        f = [r[:] for r in bg]
        for y in range(20, 34):
            for x in range(x0, x0 + 14):
                if 0 <= x < W:
                    f[y][x] = box[y - 20][x - x0]
        return f

    def gt(t):
        return frame(round(30 + dx * t))

    def mask(t):
        x0 = round(30 + dx * t)
        return {(y, x) for y in range(20, 34) for x in range(x0, x0 + 14)}

    return frame(30), frame(30 + dx), gt, mask


# ---------------- 指标 ----------------
def psnr(a, b):
    mse = sum((x - y) ** 2 for x, y in zip(a, b)) / len(a)
    return float("inf") if mse == 0 else 20 * math.log10(255 / math.sqrt(mse))


def ghost_ratio(interp, gt, mask, total, thr=10):
    bad = sum(1 for (y, x) in mask if abs(interp[y * W + x] - gt[y * W + x]) > thr)
    return bad / total, bad / len(mask)


def flatten(rows):
    return [v for row in rows for v in row]


# ---------------- 运行 ----------------
def run_case(name, f0, f1, gt_fn, mask_fn, tlist, work, block=8):
    d = os.path.join(work, name)
    os.makedirs(d, exist_ok=True)
    memc.write_pgm(os.path.join(d, "f0.pgm"), W, H, flatten(f0))
    memc.write_pgm(os.path.join(d, "f1.pgm"), W, H, flatten(f1))
    req = {"frames": [{"file": "f0.pgm"}, {"file": "f1.pgm"}],
           "t": tlist, "block": block}
    req_path = os.path.join(d, "request.json")
    json.dump(req, open(req_path, "w"))
    outdir = os.path.join(d, "out")
    env = dict(os.environ)
    env.pop("MEMC_ORDER_SEED", None)
    subprocess.run([sys.executable, os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "memc.py"), req_path, outdir],
        check=True, env=env)
    results = {}
    for t in tlist:
        _, _, interp = memc.read_pgm(os.path.join(outdir, f"interp_{t}.pgm"))
        g = flatten(gt_fn(t))
        p = psnr(interp, g)
        gr_total, gr_mask = ghost_ratio(interp, g, mask_fn(t), W * H)
        results[t] = {"psnr": p, "ghost_total": gr_total, "ghost_in_mask": gr_mask}
    stats = json.load(open(os.path.join(outdir, "stats.json")))
    return d, outdir, results, stats


def run_shuffled(req_path, outdir, seed):
    env = dict(os.environ)
    env["MEMC_ORDER_SEED"] = str(seed)
    subprocess.run([sys.executable, os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "memc.py"),
        req_path, outdir], check=True, env=env)


def files_sig(d):
    sig = {}
    for name in sorted(os.listdir(d)):
        with open(os.path.join(d, name), "rb") as f:
            sig[name] = hash(f.read())
    return sig


def main():
    work = os.path.join(tempfile.gettempdir(), "memc_selftest")
    os.makedirs(work, exist_ok=True)
    tlist = [0.25, 0.5, 0.75]
    cases = {
        "translate": scene_translate(10),
        "occlusion": scene_occlusion(),
        "zoom": scene_zoom(),
        "low_texture": scene_low_texture(),
        "repeated": scene_repeated(),
    }
    all_ok = True
    print(f"{'case':<12}{'t':>6}{'PSNR(dB)':>10}{'ghost/total':>13}"
          f"{'ghost/mask':>12}")
    for name, (f0, f1, gt_fn, mask_fn) in cases.items():
        d, outdir, results, stats = run_case(
            name, f0, f1, gt_fn, mask_fn, tlist, work)
        for t in tlist:
            r = results[t]
            ok = r["psnr"] >= 30.0 and r["ghost_total"] <= 0.01
            all_ok &= ok
            print(f"{name:<12}{t:>6}{r['psnr']:>10.2f}"
                  f"{r['ghost_total'] * 100:>12.3f}%{r['ghost_in_mask'] * 100:>11.2f}%"
                  f"  {'OK' if ok else 'FAIL'}")
        print(f"  stats: occl_ratio={stats['occl_ratio']:.4f} "
              f"fallback_px={stats['fallback_px']} mv_count={stats['mv_count']} "
              f"lowconf={stats['lowconf_px']} "
              f"direct={stats['direct_blocks_f01']} "
              f"propagated={stats['propagated_blocks_f01']}")

    # ---- 顺序无关性: 随机重排块匹配遍历顺序, 全部产物必须字节一致 ----
    base_d, base_out, _, _ = run_case(
        "order_check", *scene_occlusion(), tlist, work)
    base_sig = files_sig(base_out)
    for seed in (1, 7, 42):
        od = os.path.join(work, "order_check", f"out_seed{seed}")
        run_shuffled(os.path.join(work, "order_check", "request.json"), od, seed)
        same = files_sig(od) == base_sig
        all_ok &= same
        print(f"order-invariance seed={seed:<3}: {'OK (identical outputs)' if same else 'FAIL'}")

    # ---- 重复纹理: 条纹静态区必须零位移 (周期别名被零运动规则抑制) ----
    d = os.path.join(work, "repeated", "out")
    flow = json.load(open(os.path.join(d, "flow_f01.json")))
    bad_alias = []
    checked = 0
    for v in flow["vectors"]:
        # 条纹区 x 块起点 >= 24 且不在目标行带
        x0 = v["bx"] * flow["block"]
        y0 = v["by"] * flow["block"]
        if x0 >= 24 and not (16 <= y0 < 36):
            checked += 1
            if (v["dx"], v["dy"]) != (0, 0):
                bad_alias.append((v["bx"], v["by"], v["dx"], v["conf"]))
    ok_alias = len(bad_alias) == 0 and checked > 0
    all_ok &= ok_alias
    print(f"repeated-texture alias suppression: "
          f"{'OK' if ok_alias else 'FAIL'} ({checked} blocks, "
          f"{len(bad_alias)} aliased)")

    # ---- 低纹理: 背景静态区必须零位移, 目标区位移正确 ----
    flow = json.load(open(os.path.join(work, "low_texture", "out", "flow_f01.json")))
    bad_static = [v for v in flow["vectors"]
                  if v["by"] * flow["block"] < 8 and (v["dx"], v["dy"]) != (0, 0)]
    ok_static = not bad_static
    all_ok &= ok_static
    print(f"low-texture static region: {'OK' if ok_static else 'FAIL'} "
          f"({len(bad_static)} non-static blocks)")

    # ---- 遮挡图抽查: 遮挡穿越场景的遮挡带应被标记 ----
    _, _, occl = memc.read_pgm(os.path.join(work, "occlusion", "out", "occlusion.pgm"))
    band = [(y, x) for y in range(8, 24) for x in range(22, 42)]
    hit = sum(1 for y, x in band if occl[y * W + x] == 255) / len(band)
    print(f"occlusion band detection ratio: {hit:.3f}")

    print("\nRESULT:", "ALL PASS" if all_ok else "SOME CHECKS FAILED")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
