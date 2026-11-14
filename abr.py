#!/usr/bin/env python3
"""ABR 多档编码模拟 —— 统一 GOP 边界 / 帧级 QP 码率控制。

修复基线缺陷: 旧实现各档独立按固定帧数切 GOP, 场景切换被忽略, 导致
各档 IDR 位置漂移, 档位切换点不对齐、画面跳变。

处理流水线:
  1. 场景切换检测只做一次 (以最高档为参考, 切换信息来自源内容本身);
  2. 据此生成唯一的 GOP 分割计划: 场景切处强制 IDR 并重置固定周期计数,
     否则每 gop 帧一个 IDR; 源片尾不足一个 GOP 的余量作为收尾 GOP;
  3. 所有档位复用同一计划, 帧类型/引用结构逐帧一致, 仅帧级 QP 不同;
  4. GOP 内 VBR: GOP 预算按参考编码代价加权分配, 帧 QP 跟随帧复杂度,
     同一公式作用于每一档, 保证引用结构与最高档一致;
  5. 每个 GOP 是独立编码分块 (closed GOP), 分块编码顺序不影响拼装后的
     码流结构。

用法: python3 abr.py req.json output.json
req.json:
  {"frames":[{"t":0,"scene":0,"complexity":可选}...],
   "ladder":[{"name":"hi","rate":4000},...],
   "gop":8,"tol":0.05}
rate 单位为每帧周期的比特数 (模拟码率单位)。
"""
import json
import math
import sys

QP_MIN, QP_MAX = 10.0, 51.0      # 合法量化参数区间
QP_ALPHA = 0.5                   # GOP 内复杂度加权 (0=恒定QP, 1=恒定帧长)
I_COST = 1.6                     # IDR 帧相对 P 帧的编码代价倍率
R_CAL, QP_CAL = 1000.0, 32.0     # 标定曲线: 码率 R_CAL 对应平均 QP QP_CAL
PSNR_CLIP = (18.0, 60.0)


# ---------------------------------------------------------------- 场景/GOP 计划
def detect_scene_cuts(frames):
    """在源上做一次场景切换检测, 返回切换后首帧的全局帧序号列表。"""
    cuts = []
    prev = None
    for i, f in enumerate(frames):
        scene = f.get("scene", 0)
        if i == 0:
            cuts.append(0)            # 序列起点恒为 IDR
        elif scene != prev:
            cuts.append(i)
        prev = scene
    return cuts


def build_gop_plan(frames, gop_len, cuts):
    """生成全档位唯一的 GOP 计划。

    场景切换帧强制开启新 GOP (IDR), 并把固定周期计数器清零;
    无切换时每 gop_len 帧开启一个 GOP。片尾不足一个 GOP 的余量
    单独成为收尾 GOP。
    """
    if gop_len <= 0:
        raise ValueError("gop must be a positive integer")
    n = len(frames)
    cut_set = set(cuts)
    plan = []
    start = 0
    cadence = 0
    i = 0
    while i < n:
        is_cut = i in cut_set
        if i > start and (is_cut or cadence == gop_len):
            plan.append({"start": start, "end": i, "idr": start,
                         "frames": i - start, "scene_cut": start in cut_set})
            start = i
            cadence = 0
        cadence += 1
        i += 1
    if start < n:
        plan.append({"start": start, "end": n, "idr": start,
                     "frames": n - start, "scene_cut": start in cut_set})
    return plan


def frame_complexity(f, idx):
    """帧纹理/运动复杂度, 缺省时由场景号派生 (确定性, 无外部依赖)。"""
    c = f.get("complexity")
    if c is None:
        c = 1.0 + (f.get("scene", 0) % 7) * 0.35 + (idx % 5) * 0.08
    return float(c)


# ---------------------------------------------------------------- 帧级编码模型
def _frame_costs(frames, plan):
    """参考编码代价 A_i = 复杂度 * 帧类型倍率; 各档完全一致。"""
    costs = [0.0] * len(frames)
    for g in plan:
        for i in range(g["start"], g["end"]):
            m = I_COST if i == g["idr"] else 1.0
            costs[i] = frame_complexity(frames[i], i) * m
    return costs


def _calibration_k(costs):
    """全局唯一尺度 K, 按标定码率 R_CAL -> 平均 QP QP_CAL 反解。

    K 对所有档位相同: 高档由此得到更低 QP (更高 PSNR), 低档 QP 更高;
    而 GOP 级 qp_base 是闭式解, 无论 K 取何值都精确命中该 GOP 比特预算。
    """
    n = len(costs)
    total = sum(costs)
    if n == 0 or total <= 0:
        return 1.0
    # R_CAL*n = K * total * 2^(-QP_CAL/6)
    return R_CAL * n / (total * 2.0 ** (-QP_CAL / 6.0))


def _encode_gop(gi, gop, frames, rate, k, costs):
    """编码单个 GOP 分块。纯函数: 只依赖该 GOP 的帧与全局参数。

    码率模型: size_i = A_i * 2^(-QP_i/6), A_i = K * 复杂度 * 类型倍率。
    GOP 预算按参考代价加权 (VBR), 帧 QP 跟随帧复杂度; 未触发钳位时
    解析解保证 GOP 实际码率精确等于目标。
    """
    s, e = gop["start"], gop["end"]
    n_g = e - s
    ref_total = sum(costs)
    ref_g = sum(costs[s:e])
    target_g = rate * len(frames) * ref_g / ref_total          # GOP 比特预算

    A = [k * costs[i] for i in range(s, e)]
    a_mean = sum(A) / n_g
    denom = sum(a ** (1.0 + QP_ALPHA) for a in A)
    # 2^(-qp_base/6) = target_g * Amean^alpha / Σ A_i^(1+alpha)
    qp_base = -6.0 * math.log2(target_g * a_mean ** QP_ALPHA / denom)

    out = []
    for k_idx, i in enumerate(range(s, e)):
        is_idr = i == gop["idr"]
        qp = qp_base - QP_ALPHA * 6.0 * math.log2(A[k_idx] / a_mean)
        qp = min(QP_MAX, max(QP_MIN, qp))
        size = A[k_idx] * 2.0 ** (-qp / 6.0)
        psnr = min(PSNR_CLIP[1], max(PSNR_CLIP[0], 58.0 - 0.5 * qp))
        f = frames[i]
        out.append({
            "t": f["t"], "ts": f["t"], "dts": f["t"], "pts": f["t"],
            "gop": gi,
            "type": "I" if is_idr else "P",
            "qp": round(qp, 2),
            "size": round(size, 2),
            "psnr": round(psnr, 2),
            "ref": -1 if is_idr else frames[i - 1]["t"],
        })
    return out


def encode_rung(frames, plan, rate, k, costs, gop_order=None):
    """编码单档; gop_order 可改变分块编码顺序, 拼装结果保持不变。"""
    order = list(gop_order) if gop_order is not None else list(range(len(plan)))
    chunks = {}
    for gi in order:
        chunks[gi] = _encode_gop(gi, plan[gi], frames, rate, k, costs)
    stream = []
    for gi in range(len(plan)):
        stream.extend(chunks[gi])

    total_size = sum(fr["size"] for fr in stream)
    n = max(1, len(stream))
    rate_avg = total_size / n
    gop_stats = []
    for gi, g in enumerate(plan):
        cframes = [fr for fr in stream if fr["gop"] == gi]
        gop_stats.append({
            "start": g["start"], "end": g["end"], "idr": g["idr"],
            "frames": g["frames"], "scene_cut": g["scene_cut"],
            "types": "I" + "P" * (g["frames"] - 1),
            "qp_avg": round(sum(fr["qp"] for fr in cframes) / len(cframes), 2),
            "rate_target": round(rate, 2),
            "rate_avg": round(sum(fr["size"] for fr in cframes) / len(cframes), 2),
            "psnr_avg": round(sum(fr["psnr"] for fr in cframes) / len(cframes), 2),
        })
    return {
        "rate_target": rate,
        "rate_avg": round(rate_avg, 4),
        "rate_dev": round((rate_avg - rate) / rate, 6) if rate else 0.0,
        "frames": stream,
        "gop_stats": gop_stats,
        "switch_ok": True,
    }


# ---------------------------------------------------------------- 对齐报告
def build_alignment(plan, ladder_out, cuts, frames, tol):
    rungs = list(ladder_out.keys())
    boundary = [g["idr"] for g in plan]
    checks = []

    def check(name, ok, detail=""):
        checks.append({"name": name, "pass": bool(ok), "detail": detail})

    # 1) 各档 GOP 首帧序号/边界完全一致
    ref_starts = [(g["start"], g["end"], g["idr"]) for g in plan]
    same_bounds = True
    for name in rungs:
        got = [(s["start"], s["end"], s["idr"]) for s in ladder_out[name]["gop_stats"]]
        same_bounds &= got == ref_starts
    check("gop_boundaries_identical", same_bounds,
          "gop_count=%d boundaries=%s" % (len(plan), boundary))

    # 2) 逐档 DTS/PTS 单调
    mono = True
    for name in rungs:
        ts = [(fr["dts"], fr["pts"]) for fr in ladder_out[name]["frames"]]
        mono &= all(ts[i][0] < ts[i + 1][0] and ts[i][1] < ts[i + 1][1]
                    for i in range(len(ts) - 1))
    check("dts_pts_strictly_monotonic", mono)

    # 3) 跨档同位置帧时间戳差为零
    max_diff = 0.0
    base = ladder_out[rungs[0]]["frames"] if rungs else []
    for name in rungs:
        cur = ladder_out[name]["frames"]
        if len(cur) != len(base):
            max_diff = float("inf")
            continue
        for a, b in zip(base, cur):
            max_diff = max(max_diff, abs(a["dts"] - b["dts"]),
                           abs(a["pts"] - b["pts"]), abs(a["ts"] - b["ts"]))
    check("cross_rung_timestamp_diff_zero", max_diff == 0.0,
          "max_abs_diff=%s" % max_diff)

    # 4) 场景切换点全部落在 GOP 边界
    cut_on_boundary = all(c in boundary for c in cuts)
    check("all_scene_cuts_on_gop_boundary", cut_on_boundary, "cuts=%s" % cuts)

    # 5) 各档码率偏差
    worst = max((abs(ladder_out[n]["rate_dev"]) for n in rungs), default=0.0)
    check("rate_within_tolerance", worst <= tol + 1e-9,
          "tol=%s worst_abs_dev=%s" % (tol, round(worst, 6)))

    # 6) 引用结构逐档一致 (I/P 与 ref)
    ref_types = [fr["type"] for fr in base]
    struct_ok = all([fr["type"] for fr in ladder_out[n]["frames"]] == ref_types
                    for n in rungs)
    check("reference_structure_identical", struct_ok)

    return {
        "aligned": all(c["pass"] for c in checks),
        "reference_rung": rungs[0] if rungs else None,
        "rungs": rungs,
        "gop_count": len(plan),
        "boundary_frames": boundary,
        "switch_points": [{"gop": gi, "frame": g["idr"],
                           "ts": frames[g["idr"]]["t"],
                           "scene_cut": g["scene_cut"]}
                          for gi, g in enumerate(plan)],
        "scene_cuts": cuts,
        "timestamp_diff_max": max_diff,
        "checks": checks,
    }


# ---------------------------------------------------------------- 主流程
def run(req, gop_order=None):
    frames = req["frames"]
    gop_len = int(req["gop"])
    tol = float(req.get("tol", 0.05))
    ladder = req["ladder"]
    if not ladder:
        raise ValueError("ladder must contain at least one rung")
    names = [ld["name"] for ld in ladder]
    if len(set(names)) != len(names):
        raise ValueError("duplicate ladder rung names")

    # 最高档 = 目标码率最大档 (并列取首个), 场景检测以源/最高档为准
    top = max(range(len(ladder)), key=lambda i: ladder[i]["rate"])
    cuts = detect_scene_cuts(frames)
    plan = build_gop_plan(frames, gop_len, cuts)
    costs = _frame_costs(frames, plan)
    k = _calibration_k(costs)          # 全局唯一尺度, 各档共享

    out_ladder = {}
    for ld in ladder:
        out_ladder[ld["name"]] = encode_rung(
            frames, plan, float(ld["rate"]), k, costs, gop_order)

    return {
        "reference_rung": ladder[top]["name"],
        "gops": plan,
        "ladder": out_ladder,
        "psnr": {name: round(
                    sum(fr["psnr"] for fr in out_ladder[name]["frames"]) /
                    max(1, len(frames)), 2)
                 for name in names},
        "alignment": build_alignment(plan, out_ladder, cuts, frames, tol),
    }


def main(argv):
    if len(argv) != 3:
        print("usage: python3 abr.py req.json output.json", file=sys.stderr)
        return 2
    with open(argv[1]) as fh:
        req = json.load(fh)
    res = run(req)
    with open(argv[2], "w") as fh:
        json.dump(res, fh, indent=1, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
