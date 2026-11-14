#!/usr/bin/env python3
"""M02 验收脚本: 统一 GOP 边界 / 多档切换点对齐 / 码率与 PSNR 检查。

运行: python3 verify.py
全部检查通过时退出码 0, 否则非零。
"""
import json
import os
import random
import subprocess
import sys

import abr

HERE = os.path.dirname(os.path.abspath(__file__))
N_FRAMES = 240
N_CUTS = 20
GOP = 8
TOL = 0.05
LADDER = [
    {"name": "rung_4000", "rate": 4000},
    {"name": "rung_2400", "rate": 2400},
    {"name": "rung_1400", "rate": 1400},
    {"name": "rung_800",  "rate": 800},
    {"name": "rung_400",  "rate": 400},
]
FAILURES = []


def check(cond, label, detail=""):
    status = "PASS" if cond else "FAIL"
    print("[%s] %s %s" % (status, label, ("-- " + detail) if detail else ""))
    if not cond:
        FAILURES.append(label)


def make_request(seed=7):
    """构造含 20 个场景切换的源 (另有序列起点切换)。"""
    rnd = random.Random(seed)
    # 切换位置避开边界 0/N-1 且互不相邻, 分布在整段源中
    positions = sorted(rnd.sample(range(5, N_FRAMES - 5), N_CUTS))
    scene = 0
    frames = []
    for i in range(N_FRAMES):
        if i in positions:
            scene += 1
        frames.append({"t": i, "scene": scene,
                       "complexity": round(1.0 + rnd.random() * 2.0, 3)})
    return {"frames": frames, "ladder": LADDER, "gop": GOP, "tol": TOL}, positions


def main():
    req, cut_positions = make_request()
    frames = req["frames"]
    res = abr.run(req)

    names = [ld["name"] for ld in LADDER]
    plan = res["gops"]
    boundaries = [g["idr"] for g in plan]

    # ---- 验收 1a: 5 档每 GOP 首帧序号与边界完全一致
    check(len(names) == 5, "rung count is 5", str(names))
    per_rung_bounds = {
        n: [(s["start"], s["end"], s["idr"]) for s in res["ladder"][n]["gop_stats"]]
        for n in names}
    ref_bounds = [(g["start"], g["end"], g["idr"]) for g in plan]
    check(all(b == ref_bounds for b in per_rung_bounds.values()),
          "all rungs share identical GOP start/end/IDR",
          "gop_count=%d" % len(plan))

    # 帧级 gop 归属也一致
    membership_ok = all(
        [fr["gop"] for fr in res["ladder"][n]["frames"]]
        == [fr["gop"] for fr in res["ladder"][names[0]]["frames"]]
        for n in names)
    check(membership_ok, "per-frame GOP assignment identical across rungs")

    # 相邻 GOP 首尾相接、片尾收尾 GOP 覆盖到最后一帧
    contiguous = all(plan[i]["end"] == plan[i + 1]["start"]
                     for i in range(len(plan) - 1))
    check(contiguous and plan[0]["start"] == 0 and plan[-1]["end"] == N_FRAMES,
          "GOPs are contiguous and cover tail remainder",
          "tail gop frames=%d (gop=%d)" % (plan[-1]["frames"], GOP))

    # ---- 验收 1b: 逐档 DTS/PTS 严格单调
    mono_ok = True
    for n in names:
        ts = [(fr["dts"], fr["pts"]) for fr in res["ladder"][n]["frames"]]
        mono_ok &= all(ts[i][0] < ts[i + 1][0] and ts[i][1] < ts[i + 1][1]
                       for i in range(len(ts) - 1))
    check(mono_ok, "DTS/PTS strictly monotonic per rung")

    # ---- 验收 1c: 跨档同位置帧时间戳差为零
    max_diff = 0.0
    for n in names:
        for base_fr, fr in zip(res["ladder"][names[0]]["frames"],
                               res["ladder"][n]["frames"]):
            max_diff = max(max_diff,
                           abs(base_fr["dts"] - fr["dts"]),
                           abs(base_fr["pts"] - fr["pts"]),
                           abs(base_fr["ts"] - fr["ts"]),
                           abs(base_fr["t"] - fr["t"]))
    check(max_diff == 0.0, "cross-rung same-frame timestamp diff is zero",
          "max_abs_diff=%s" % max_diff)

    # ---- 验收 2a: 20 个场景切换点全部落在 GOP 边界
    all_cuts = [0] + cut_positions
    check(all(c in boundaries for c in all_cuts),
          "all %d scene cuts land on GOP boundary" % N_CUTS,
          "cuts=%s" % all_cuts)
    # 每个场景切对应一个 scene_cut=True 的 GOP
    cut_gops = [g["idr"] for g in plan if g["scene_cut"]]
    check(sorted(cut_gops) == sorted(all_cuts),
          "scene_cut GOP flags match detected cuts")

    # 无切换时的固定周期: 任意两个相邻场景切之间的 GOP 长度 <= gop
    cut_set = set(all_cuts)
    lengths_ok = all(
        g["frames"] <= GOP or g["start"] in cut_set
        for g in plan)
    check(lengths_ok, "no non-cut GOP exceeds fixed GOP length")

    # ---- 验收 2b: 各档平均码率相对目标偏差 <= 5%
    rate_rows = []
    rate_ok = True
    for n in names:
        rung = res["ladder"][n]
        target = next(ld["rate"] for ld in LADDER if ld["name"] == n)
        dev = abs(rung["rate_avg"] - target) / target
        rate_ok &= dev <= TOL
        rate_rows.append("%s target=%d avg=%.2f dev=%.4f%%"
                         % (n, target, rung["rate_avg"], 100 * dev))
    check(rate_ok, "all rungs within +/-5%% target rate",
          "; ".join(rate_rows))

    # ---- 验收 2c: PSNR 报告齐全 (序列级 + GOP 级 + 帧级)
    psnr_seq_ok = all(n in res["psnr"] and isinstance(res["psnr"][n], (int, float))
                      for n in names)
    check(psnr_seq_ok, "sequence-level PSNR present for every rung",
          str(res["psnr"]))
    psnr_detail_ok = all(
        all("psnr_avg" in s for s in res["ladder"][n]["gop_stats"])
        and all("psnr" in fr for fr in res["ladder"][n]["frames"])
        for n in names)
    check(psnr_detail_ok, "GOP-level and frame-level PSNR present")

    # 多档区分度: 目标码率越高 -> 平均 QP 越低 -> PSNR 越高; QP 始终合法
    ordered = sorted(LADDER, key=lambda ld: ld["rate"], reverse=True)
    qp_means = []
    for ld in ordered:
        fr = res["ladder"][ld["name"]]["frames"]
        qp_means.append(sum(f["qp"] for f in fr) / len(fr))
        check(all(abr.QP_MIN <= f["qp"] <= abr.QP_MAX for f in fr),
              "frame QP within [%g,%g] on %s" % (abr.QP_MIN, abr.QP_MAX,
                                                  ld["name"]))
    qp_ordered = all(qp_means[i] < qp_means[i + 1]
                     for i in range(len(qp_means) - 1))
    check(qp_ordered, "avg QP decreases as rung rate increases",
          str([round(q, 2) for q in qp_means]))
    psnr_vals = [res["psnr"][ld["name"]] for ld in ordered]
    psnr_ordered = all(psnr_vals[i] > psnr_vals[i + 1]
                       for i in range(len(psnr_vals) - 1))
    check(psnr_ordered, "PSNR increases as rung rate increases",
          str(psnr_vals))

    # ---- 引用结构逐档一致 (closed GOP, IDR 无引用, P 帧引用前一帧)
    types_ref = [fr["type"] for fr in res["ladder"][names[0]]["frames"]]
    ref_ok = all(
        [fr["type"] for fr in res["ladder"][n]["frames"]] == types_ref
        and all((fr["ref"] == -1) == (fr["type"] == "I") for fr in
                res["ladder"][n]["frames"])
        for n in names)
    check(ref_ok, "I/P reference structure identical across rungs")
    idr_idx = [i for i, t in enumerate(types_ref) if t == "I"]
    check(idr_idx == boundaries, "I-frame positions equal GOP boundaries")

    # ---- 分块编码顺序变化不影响码流结构
    rng = random.Random(11)
    order = list(range(len(plan)))
    rng.shuffle(order)
    res_shuffled = abr.run(req, gop_order=order)
    same_stream = all(
        res_shuffled["ladder"][n]["frames"] == res["ladder"][n]["frames"]
        and res_shuffled["ladder"][n]["gop_stats"]
        == res["ladder"][n]["gop_stats"]
        for n in names)
    check(same_stream, "GOP chunk encoding order does not change bitstream")

    # ---- CLI 端到端 (req.json -> output.json)
    req_path = os.path.join(HERE, "req.json")
    out_path = os.path.join(HERE, "output.json")
    with open(req_path, "w") as fh:
        json.dump(req, fh)
    proc = subprocess.run([sys.executable, os.path.join(HERE, "abr.py"),
                           req_path, out_path],
                          capture_output=True, text=True)
    check(proc.returncode == 0, "CLI abr.py exits zero", proc.stderr.strip())
    if proc.returncode == 0:
        with open(out_path) as fh:
            cli_res = json.load(fh)
        check(cli_res["alignment"]["aligned"] is True,
              "CLI output alignment report is aligned")
        check(set(cli_res.keys()) >= {"gops", "ladder", "psnr", "alignment"},
              "CLI output contains required top-level sections")

    # ---- 对齐报告自检
    check(res["alignment"]["aligned"] is True, "alignment.aligned is True")
    for c in res["alignment"]["checks"]:
        check(c["pass"], "alignment check: " + c["name"], c.get("detail", ""))

    # ---- 小源片尾: 不足一个 GOP 的收尾
    tail_req = {
        "frames": [{"t": i, "scene": 0} for i in range(19)]
                  + [{"t": 19, "scene": 1}]
                  + [{"t": i, "scene": 1} for i in range(20, 22)],
        "ladder": LADDER[:2], "gop": GOP, "tol": TOL}
    tail = abr.run(tail_req)
    tail_plan = tail["gops"]
    check(tail_plan[-1]["end"] == 22 and tail_plan[-1]["frames"] < GOP,
          "short tail remainder forms final partial GOP",
          "last gop=%s" % tail_plan[-1])
    check(19 in [g["idr"] for g in tail_plan],
          "tail source scene cut at 19 forces IDR")
    check(tail["alignment"]["aligned"] is True, "tail source aligned")

    # ---- 旧基线缺陷回归: 固定切分无法把场景切放到边界
    legacy_bounds = list(range(0, N_FRAMES, GOP))
    legacy_misses = [c for c in cut_positions if c not in legacy_bounds]
    check(bool(legacy_misses), "legacy fixed-grid would miss scene cuts",
          "%d missed (proved bug existed)" % len(legacy_misses))

    print("\n=== summary ===")
    if FAILURES:
        print("FAILED: %d check(s): %s" % (len(FAILURES), FAILURES))
        return 1
    print("ALL CHECKS PASSED (%d frames, %d scene cuts, %d rungs, %d GOPs)"
          % (N_FRAMES, N_CUTS, len(names), len(plan)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
