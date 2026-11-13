"""体积雾积分的独立验收脚本（仅标准库）。

三类参考相互独立于 fog.py 的实现：
  1. 常密度/分段均匀体（le=0）：逐均匀段解析公式；
  2. 一般线性密度：独立细步长中点积分（不共享 fog.py 的任何代码路径）；
  3. 跳过/不跳过两种模式互相比对，并审计步长、覆盖与表面截断。

输出：
  outputs/fixed_rays.json     固定射线集逐射线结果与误差
  outputs/scene_skip.json     合成场景（跳过）
  outputs/scene_noskip.json   合成场景（不跳过）
  outputs/scene_skip.ppm      跳过模式透射/散射/合成三带图
  outputs/scene_noskip.ppm    不跳过模式三带图
  outputs/scene_diff.ppm      两种模式的放大差异图
  outputs/report.txt          验收汇总
"""

import json
import math
import os
from pathlib import Path

import fog

HERE = Path(__file__).resolve().parent
OUT = HERE / "outputs"
T_TRANSMIT = 0.005
T_COLOR = 0.01


# ---------------------------------------------------------------- 独立参考
def _sample_block(blocks, x):
    """返回 x 处 (rho, light, le)，间隙返回全零。"""
    for blk in blocks:
        a, b = blk["a"], blk["b"]
        if a <= x < b:
            f = 0.0 if b == a else (x - a) / (b - a)
            r0, r1 = blk["rho"]
            l0, l1 = blk["light"]
            le = blk.get("light_extinction", 0.0)
            rho = r0 + (r1 - r0) * f
            light = (l0 + (l1 - l0) * f) * math.exp(-le * (x - a))
            return rho, light, le
    return 0.0, 0.0, 0.0


def reference_fine(ray, samples_per_unit=1000.0):
    """独立细步长中点积分：T 用中点光学深度，散射用中点被积函数。

    与 fog.py 的自适应 Gauss-Legendre 路径完全独立。
    """
    near = float(ray["near"])
    cutoff = min(float(ray["far"]),
                 float(ray["surface_t"]) if ray.get("surface_t") is not None
                 else float(ray["far"]))
    blocks = ray.get("medium", [])
    n = max(1, int(math.ceil((cutoff - near) * samples_per_unit)))
    ds = (cutoff - near) / n

    tau = 0.0
    scatter = 0.0
    for i in range(n):
        x = near + (i + 0.5) * ds
        rho, light, _ = _sample_block(blocks, x)
        tau += rho * ds
        # 中点处的当前透射，前向累加
        trans = math.exp(-tau + 0.5 * rho * ds)
        scatter += trans * rho * light * ds
    transmit = math.exp(-tau)
    color = scatter + transmit * float(ray["surface_color"])
    return {"transmit": transmit, "scatter": scatter, "color": color}


def reference_analytic(ray):
    """分段均匀体（rho、light 端点相等且 le=0）的逐段解析解。"""
    near = float(ray["near"])
    cutoff = min(float(ray["far"]),
                 float(ray["surface_t"]) if ray.get("surface_t") is not None
                 else float(ray["far"]))
    transmit = 1.0
    scatter = 0.0

    # 事件点：积分端点 + 所有块边界
    events = {near, cutoff}
    for blk in ray.get("medium", []):
        events.add(max(near, min(cutoff, float(blk["a"]))))
        events.add(max(near, min(cutoff, float(blk["b"]))))
    events = sorted(x for x in events if near <= x <= cutoff)

    for lo, hi in zip(events[:-1], events[1:]):
        if hi <= lo:
            continue
        mid = 0.5 * (lo + hi)
        rho, light, _ = _sample_block(ray.get("medium", []), mid)
        length = hi - lo
        if rho == 0.0:
            continue
        optical = rho * length
        seg_trans = math.exp(-optical)
        # 均匀段内 L 恒定：dS = T_lo * L * (1 - exp(-rho*len))
        scatter += transmit * light * (1.0 - seg_trans)
        transmit *= seg_trans

    color = scatter + transmit * float(ray["surface_color"])
    return {"transmit": transmit, "scatter": scatter, "color": color}


# ---------------------------------------------------------------- 固定射线集
def _homog(a, b, rho, light, le=0.0):
    return {"a": a, "b": b, "rho": [rho, rho], "light": [light, light],
            "light_extinction": le}


def _linear(a, b, r0, r1, l0, l1, le=0.0):
    return {"a": a, "b": b, "rho": [r0, r1], "light": [l0, l1],
            "light_extinction": le}


def build_fixed_rays():
    """覆盖常密度、分段均匀、一般线性、薄雾、浓块、间隙、表面截断与边界。"""
    rays = []

    def add(name, ray, kind):
        ray = dict(ray)
        ray["name"] = name
        ray["kind"] = kind
        rays.append(ray)

    # --- 解析组：常密度与分段均匀（le=0）---
    add("const-basic",
        {"near": 0, "far": 8, "surface_color": 0.4,
         "medium": [_homog(1.0, 5.0, 0.6, 0.8)]}, "analytic")
    add("const-surface-inside",
        {"near": 0, "far": 9, "surface_t": 3.2, "surface_color": 0.25,
         "medium": [_homog(1.0, 6.0, 0.7, 0.9)]}, "analytic")
    add("const-near-clipped",
        {"near": 2.3, "far": 7.7, "surface_color": 0.5,
         "medium": [_homog(0.0, 4.0, 0.5, 0.6), _homog(4.0, 10.0, 1.2, 0.3)]},
        "analytic")
    add("piecewise-gaps",
        {"near": 0, "far": 12, "surface_t": 11.0, "surface_color": 0.15,
         "medium": [_homog(1.0, 2.5, 0.3, 1.0), _homog(4.0, 5.0, 0.0, 0.0),
                    _homog(6.5, 9.0, 1.4, 0.4)]}, "analytic")
    add("piecewise-thick",
        {"near": 0, "far": 6, "surface_color": 0.7,
         "medium": [_homog(0.5, 1.5, 2.5, 0.9),
                    _homog(2.0, 4.0, 0.15, 0.5)]}, "analytic")
    add("boundary-at-step",
        # 块边界恰好落在 step 整数倍上（step=0.25，边界 1.0/2.5）
        {"near": 0, "far": 4, "surface_color": 0.3,
         "medium": [_homog(0.0, 1.0, 0.8, 0.6),
                    _homog(1.0, 2.5, 0.2, 1.0),
                    _homog(2.5, 4.0, 1.1, 0.2)]}, "analytic")
    add("short-intervals",
        {"near": 0, "far": 3, "surface_color": 0.6,
         "medium": [_homog(0.10, 0.16, 0.9, 0.7),
                    _homog(0.16, 0.21, 0.3, 0.4),
                    _homog(0.90, 0.93, 1.7, 0.5)]}, "analytic")
    add("empty-only",
        {"near": 1, "far": 5, "surface_t": 5, "surface_color": 0.33,
         "medium": [_homog(2.0, 3.0, 0.0, 0.0)]}, "analytic")
    add("surface-before-medium",
        # 表面之后的雾必须完全不参与
        {"near": 0, "far": 10, "surface_t": 2.0, "surface_color": 0.2,
         "medium": [_homog(1.0, 3.5, 1.3, 0.9),
                    _homog(5.0, 8.0, 2.0, 1.0)]}, "analytic")
    add("surface-at-boundary",
        {"near": 0, "far": 8, "surface_t": 3.0, "surface_color": 0.45,
         "medium": [_homog(0.0, 3.0, 0.6, 0.8),
                    _homog(3.0, 7.0, 1.5, 0.5)]}, "analytic")

    # --- 一般线性密度/光源 + light_extinction：用细步长参考 ---
    add("linear-ramp",
        {"near": 0, "far": 8, "surface_color": 0.4,
         "medium": [_linear(1.0, 5.0, 0.2, 1.6, 0.9, 0.2)]}, "fine")
    add("linear-with-attenuation",
        {"near": 0, "far": 9, "surface_t": 7.5, "surface_color": 0.3,
         "medium": [_linear(2.0, 6.0, 1.2, 0.1, 1.0, 1.0, le=0.35)]}, "fine")
    add("linear-mixed-blocks",
        {"near": 0.5, "far": 10, "surface_color": 0.55,
         "medium": [_linear(1.0, 3.0, 0.0, 0.8, 0.2, 1.0),
                    _linear(4.5, 7.5, 1.5, 0.3, 0.9, 0.1, le=0.2),
                    _homog(8.0, 9.5, 0.4, 0.6)]}, "fine")
    add("linear-thin-sparse",
        # 稀疏薄雾层：大量真空 + 极低密度短块
        {"near": 0, "far": 20, "surface_color": 0.5,
         "medium": [_linear(2.0, 2.15, 0.01, 0.02, 0.8, 0.9),
                    _linear(8.0, 8.10, 0.0, 0.03, 0.7, 0.7),
                    _linear(14.0, 14.2, 0.02, 0.0, 1.0, 0.6)]}, "fine")
    add("linear-dense-then-thin",
        {"near": 0, "far": 10, "surface_t": 9.2, "surface_color": 0.25,
         "medium": [_linear(1.0, 3.0, 3.0, 2.0, 0.95, 0.9),
                    _linear(3.0, 8.0, 0.05, 0.15, 0.4, 0.8, le=0.15)]},
        "fine")
    add("linear-surface-inside-block",
        {"near": 0, "far": 10, "surface_t": 4.37, "surface_color": 0.38,
         "medium": [_linear(1.0, 7.0, 0.4, 1.8, 1.0, 0.2)]}, "fine")

    return rays


# ---------------------------------------------------------------- 覆盖审计
def audit_coverage(ray, result, step):
    """检查 trace 子段连续覆盖 [near, cutoff)，且每段不超过 step。"""
    near = float(ray["near"])
    cutoff = min(float(ray["far"]),
                 float(ray["surface_t"]) if ray.get("surface_t") is not None
                 else float(ray["far"]))
    trace = result["trace"]
    if cutoff <= near:
        return trace == []
    prev = near
    for kind, lo, hi in trace:
        if abs(lo - prev) > 1e-12 or hi <= lo:
            return False
        # skip 记录整体跨越空区，允许超过 step；实际积分子段不得超过
        if kind != "skip" and (hi - lo) > step + 1e-12:
            return False
        prev = hi
    return abs(prev - cutoff) <= 1e-12


# ---------------------------------------------------------------- PPM 图像
def _tone(x):
    x = max(0.0, min(1.0, x))
    return int(round(255.0 * (1.0 - math.exp(-2.0 * x))))


def write_band_ppm(path, rows_by_band):
    """rows_by_band: 三个列表（T、S、color），每个为每射线一个标量。"""
    n = len(rows_by_band[0])
    height = 3 * 18
    lines = ["P3", str(n), str(height), "255"]
    for band in rows_by_band:
        pixels = []
        for v in band:
            g = _tone(v)
            pixels.append((g, g, g))
        for _ in range(18):
            lines.append(" ".join("%d %d %d" % p for p in pixels))
    Path(path).write_text("\n".join(lines) + "\n")


def write_diff_ppm(path, values_a, values_b):
    n = len(values_a)
    height = 18
    lines = ["P3", str(n), str(height), "255"]
    pixels = []
    for a, b in zip(values_a, values_b):
        d = min(1.0, abs(a - b) * 50.0)  # 放大 50 倍便于观察
        g = int(round(255 * d))
        pixels.append((g, 0, 0))
    for _ in range(height):
        lines.append(" ".join("%d %d %d" % p for p in pixels))
    Path(path).write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------- 合成场景
def build_composite_scene():
    """160 条射线：薄雾层 + 浓密地面雾 + 真空间隙 + 表面遮挡序列。"""
    rays = []
    n = 160
    for i in range(n):
        # 表面深度在 3~9 之间扫动，制造雾前/雾中/雾后遮挡序列
        surface_t = 3.0 + 6.0 * i / (n - 1)
        medium = [
            _linear(0.5, 1.4, 0.02, 0.05, 0.7, 0.9),          # 高空薄雾
            _homog(2.0, 2.6, 0.0, 0.0),                        # 真空间隙
            _linear(3.2, 5.0, 0.08, 0.25, 0.9, 0.5, le=0.1),   # 光柱过渡
            _homog(5.0, 5.4, 0.0, 0.0),                        # 真空间隙
            _linear(5.4, 8.2, 0.6, 2.2, 0.8, 0.25),            # 浓密地面雾
            _linear(8.2, 9.5, 0.1, 0.1, 0.5, 0.5),             # 均匀低雾
        ]
        rays.append({"near": 0.0, "far": 10.0,
                     "surface_t": round(surface_t, 5),
                     "surface_color": 0.35 + 0.2 * (i % 7) / 6.0,
                     "medium": medium})
    return {"step": 0.25, "rays": rays}


# ---------------------------------------------------------------- 主流程
def main():
    OUT.mkdir(exist_ok=True)
    step = 0.25
    fixed = build_fixed_rays()
    scene = {"step": step, "rays": [{k: v for k, v in r.items()
                                     if k not in ("name", "kind")}
                                    for r in fixed]}

    failures = []
    rows = []
    max_t_err = 0.0
    max_c_err = 0.0
    total_skip_steps = 0
    total_noskip_steps = 0

    for idx, ray in enumerate(fixed):
        ref = (reference_analytic(ray) if ray["kind"] == "analytic"
               else reference_fine(ray))
        row = {"name": ray["name"], "kind": ray["kind"], "reference": ref}
        for mode, flag in (("skip", True), ("noskip", False)):
            res = fog.integrate_ray(scene["rays"][idx], step, flag, trace=True)
            finite = all(math.isfinite(res[k])
                         for k in ("transmit", "scatter", "color"))
            covered = audit_coverage(scene["rays"][idx], res, step)
            t_err = abs(res["transmit"] - ref["transmit"])
            c_err = abs(res["color"] - ref["color"])
            s_err = abs(res["scatter"] - ref["scatter"])
            max_t_err = max(max_t_err, t_err)
            max_c_err = max(max_c_err, c_err)
            row[mode] = {k: v for k, v in res.items() if k != "trace"}
            row[mode]["trace_len"] = len(res["trace"])
            row[mode]["finite"] = finite
            row[mode]["covered"] = covered
            row[mode]["t_err"] = t_err
            row[mode]["c_err"] = c_err
            row[mode]["s_err"] = s_err
            if not finite:
                failures.append("%s/%s 出现非有限输出" % (ray["name"], mode))
            if not covered:
                failures.append("%s/%s 子段未连续覆盖或超长" % (ray["name"], mode))
            if t_err > T_TRANSMIT:
                failures.append("%s/%s 透射误差 %.4f 超阈值"
                                % (ray["name"], mode, t_err))
            if c_err > T_COLOR:
                failures.append("%s/%s 色误差 %.4f 超阈值"
                                % (ray["name"], mode, c_err))
        # 两种模式的物理量必须一致
        for key in ("transmit", "scatter", "color"):
            d = abs(row["skip"][key] - row["noskip"][key])
            if d > 1e-12:
                failures.append("%s 两模式 %s 不一致 (%g)"
                                % (ray["name"], key, d))
        total_skip_steps += row["skip"]["steps"]
        total_noskip_steps += row["noskip"]["steps"]
        rows.append(row)

    # 表面截断：surface_t 之后不得出现任何雾记录
    for idx, ray in enumerate(fixed):
        st = ray.get("surface_t")
        if st is None:
            continue
        res = fog.integrate_ray(scene["rays"][idx], step, True, trace=True)
        for kind, lo, hi in res["trace"]:
            if hi > float(st) + 1e-12 or lo >= float(st):
                failures.append("%s 表面 %s 之后存在子段 %s"
                                % (ray["name"], st, (kind, lo, hi)))
        # 与“把表面后雾块删掉”的场景等价
        clipped = json.loads(json.dumps(scene["rays"][idx]))
        cutoff = min(float(clipped["far"]), float(st))
        clipped["medium"] = [b for b in clipped["medium"]
                             if float(b["a"]) < cutoff]
        ref_cut = fog.integrate_ray(clipped, step, True)
        got = fog.integrate_ray(scene["rays"][idx], step, True)
        if abs(got["scatter"] - ref_cut["scatter"]) > 1e-12:
            failures.append("%s 表面后雾仍贡献散射" % ray["name"])

    # 合成场景：稀疏薄雾 + 表面遮挡，验证步数下降 >=30%
    comp = build_composite_scene()
    comp_skip = fog.integrate_scene(comp, True)
    comp_noskip = fog.integrate_scene(comp, False)
    cs = sum(r["steps"] for r in comp_skip)
    cn = sum(r["steps"] for r in comp_noskip)
    reduction = 1.0 - cs / cn
    if reduction < 0.30:
        failures.append("合成场景步数仅下降 %.1f%%，未达 30%%" % (100 * reduction))
    for a, b in zip(comp_skip, comp_noskip):
        for key in ("transmit", "scatter", "color"):
            if abs(a[key] - b[key]) > 1e-12:
                failures.append("合成场景两模式 %s 不一致" % key)
                break
        if not all(math.isfinite(a[k]) for k in ("transmit", "scatter", "color")):
            failures.append("合成场景出现非有限输出")
            break

    # 细步长参考独立复核合成场景的一个子集
    for idx in (0, 17, 40, 80, 119, 159):
        ref = reference_fine(comp["rays"][idx], samples_per_unit=2000.0)
        got = comp_skip[idx]
        if abs(got["transmit"] - ref["transmit"]) > T_TRANSMIT:
            failures.append("合成射线 %d 透射误差超阈值" % idx)
        if abs(got["color"] - ref["color"]) > T_COLOR:
            failures.append("合成射线 %d 色误差超阈值" % idx)

    # 落盘
    (OUT / "fixed_rays.json").write_text(
        json.dumps({"tolerance": {"transmit": T_TRANSMIT, "color": T_COLOR},
                    "rows": rows}, ensure_ascii=False, indent=1) + "\n")
    (OUT / "scene_skip.json").write_text(
        json.dumps({"rays": comp_skip}, allow_nan=False) + "\n")
    (OUT / "scene_noskip.json").write_text(
        json.dumps({"rays": comp_noskip}, allow_nan=False) + "\n")
    write_band_ppm(OUT / "scene_skip.ppm",
                   ([r["transmit"] for r in comp_skip],
                    [r["scatter"] for r in comp_skip],
                    [r["color"] for r in comp_skip]))
    write_band_ppm(OUT / "scene_noskip.ppm",
                   ([r["transmit"] for r in comp_noskip],
                    [r["scatter"] for r in comp_noskip],
                    [r["color"] for r in comp_noskip]))
    write_diff_ppm(OUT / "scene_diff.ppm",
                   [r["color"] for r in comp_skip],
                   [r["color"] for r in comp_noskip])

    report = []
    report.append("固定射线集: %d 条（解析 %d / 细步长 %d）"
                  % (len(fixed),
                     sum(1 for r in fixed if r["kind"] == "analytic"),
                     sum(1 for r in fixed if r["kind"] == "fine")))
    report.append("最大透射绝对误差: %.2e (阈值 %g)" % (max_t_err, T_TRANSMIT))
    report.append("最大线性色误差:   %.2e (阈值 %g)" % (max_c_err, T_COLOR))
    report.append("固定集步数 跳过=%d 不跳过=%d"
                  % (total_skip_steps, total_noskip_steps))
    report.append("合成场景步数 跳过=%d 不跳过=%d 下降=%.1f%%"
                  % (cs, cn, 100 * reduction))
    report.append("合成场景跳过空块计数合计=%d"
                  % sum(r["skipped"] for r in comp_skip))
    report.append("输出文件: " + ", ".join(sorted(os.listdir(OUT))))
    report.append("结论: " + ("全部通过" if not failures else "存在失败"))
    if failures:
        report.append("失败项:")
        report.extend("  - " + f for f in failures)
    text = "\n".join(report)
    (OUT / "report.txt").write_text(text + "\n")
    print(text)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())