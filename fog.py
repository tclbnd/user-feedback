"""离线体积雾积分（仅依赖 Python 标准库）。

沿一维视线对分段线性介质做透射率 T 与单次散射 S 的积分：

    T(x)   = exp(-integral rho)
    dS     = T(x) * rho(x) * L(x) dx
    L(x)   = light(x) * exp(-light_extinction * (x - a))

介质按半开块 [a, b) 描述，块内消光系数 rho 与光源亮度 light 均为线性插值，
light_extinction 为块内局部光源衰减。视线积分区间为
[near, min(far, surface_t))，即首个不透明表面之后不存在任何雾贡献；
没有 surface_t 时 surface_color 充当远处背景。

积分被严格截断在 near/far、表面深度与块边界上，因此每个积分子段长度都不超过
场景 step 且完全落在同一个介质块内，步长随边界/表面自然变化，不会重复或遗漏
任何有效区间。空区 = 块间真空间隙，以及两个 rho 端点都为零的块；开启跳过时
空区被整体越过（skipped 按被越过的非零长度空区计数），关闭时空区也按 step
分段并计入 steps，但不改变 T 与 S。

用法：
    python3 fog.py scene.json output.json [--no-skip]
"""

import argparse
import json
import math
from pathlib import Path


# Gauss-Legendre 求积节点/权重（区间 [-1, 1]），用于段内散射积分
_GL8_X = (-0.9602898564975363, -0.7966664774136267, -0.5255324099163290,
          -0.1834346424956498, 0.1834346424956498, 0.5255324099163290,
          0.7966664774136267, 0.9602898564975363)
_GL8_W = (0.1012285362903763, 0.2223810344533745, 0.3137066458778873,
          0.3626837833783620, 0.3626837833783620, 0.3137066458778873,
          0.2223810344533745, 0.1012285362903763)
_GL4_X = (-0.8611363115940526, -0.3399810435848563,
          0.3399810435848563, 0.8611363115940526)
_GL4_W = (0.3478548451374538, 0.6521451548625461,
          0.6521451548625461, 0.3478548451374538)
_MAX_RECURSE = 24


def _gl_quadrature(integrand, lo, hi, nodes, weights):
    """在 [lo, hi] 上做固定阶 Gauss-Legendre 求积。"""
    half = 0.5 * (hi - lo)
    mid = 0.5 * (hi + lo)
    total = 0.0
    for xi, wi in zip(nodes, weights):
        total += wi * integrand(mid + half * xi)
    return half * total


def _scatter_integral(block, u0, u1, rho0):
    """计算段 [u0, u1) 内 integral exp(-tau(s)) * rho(s) * L(s) ds（段起点透射为 1）。

    block 保存块起点 a 与线性场参数；rho0 为段起点消光系数。被积函数光滑
    （指数型二次式乘多项式），用 GL8/GL4 对比做确定性自适应细分，厚雾段也能
    保持精度；这些内部求积细分不计入外层步进 steps。
    """
    a, r0, slope_r, l0, slope_l, le = block
    z0 = u0 - a
    length = u1 - u0

    def integrand(s):
        # 从段起点累计的光学深度，rho 线性 => tau 为二次式
        tau = rho0 * s + 0.5 * slope_r * s * s
        rho = rho0 + slope_r * s
        z = z0 + s
        light = (l0 + slope_l * z) * math.exp(-le * z)
        return math.exp(-tau) * rho * light

    def recurse(lo, hi, depth):
        value8 = _gl_quadrature(integrand, lo, hi, _GL8_X, _GL8_W)
        value4 = _gl_quadrature(integrand, lo, hi, _GL4_X, _GL4_W)
        tolerance = 1e-10 + 1e-9 * max(abs(value8), abs(value4))
        if depth == 0 or abs(value8 - value4) <= tolerance:
            return value8
        middle = 0.5 * (lo + hi)
        return recurse(lo, middle, depth - 1) + recurse(middle, hi, depth - 1)

    return recurse(0.0, length, _MAX_RECURSE)


def _integrate_segment(transmit, scatter, block, u0, u1):
    """在同一个介质块内积分子段 [u0, u1)，统一更新透射与单次散射。"""
    a, r0, slope_r, l0, slope_l, le = block
    z0 = u0 - a
    length = u1 - u0
    rho0 = r0 + slope_r * z0
    rho1 = r0 + slope_r * (z0 + length)

    # rho 线性，梯形公式即解析精确的段光学深度
    optical = 0.5 * (rho0 + rho1) * length
    scatter += transmit * _scatter_integral(block, u0, u1, rho0)
    transmit *= math.exp(-optical)
    return transmit, scatter


def _build_intervals(ray, near, cutoff):
    """把介质块裁剪到 [near, cutoff)，并与真空间隙合并成有序、不重叠、
    首尾恰好覆盖积分区间的 (lo, hi, block, is_empty) 列表。"""
    intervals = []
    cursor = near
    raw_blocks = sorted(ray.get("medium", []), key=lambda span: float(span["a"]))

    for span in raw_blocks:
        a = float(span["a"])
        b = float(span["b"])
        if b <= a:
            continue
        length = b - a
        r0 = float(span["rho"][0])
        r1 = float(span["rho"][1])
        l0 = float(span["light"][0])
        l1 = float(span["light"][1])
        le = float(span.get("light_extinction", 0.0))
        slope_r = (r1 - r0) / length
        slope_l = (l1 - l0) / length

        lo = max(a, near)
        hi = min(b, cutoff)
        if hi <= lo:
            continue

        # 裁剪后重新取端点场值，空块判定以裁剪后的两个 rho 端点为准
        rho_lo = r0 + slope_r * (lo - a)
        rho_hi = r0 + slope_r * (hi - a)
        is_empty = (rho_lo == 0.0 and rho_hi == 0.0)
        # 保留原始块起点 a，light_extinction 的距离始终相对原始 a 计算
        block = (a, r0, slope_r, l0, slope_l, le)

        if cursor < lo:
            intervals.append((cursor, lo, None, True))
        intervals.append((lo, hi, block, is_empty))
        cursor = hi

    if cursor < cutoff:
        intervals.append((cursor, cutoff, None, True))
    return intervals


def integrate_ray(ray, step, skip_empty, trace=False):
    """对单条射线积分，返回 transmit / scatter / color / steps / skipped。

    trace=True 时额外返回子段记录列表 (kind, lo, hi)，kind 取
    dense（实际积分）、empty（未跳过时的空区分段）或 skip（整体跳过）。
    """
    if step <= 0.0:
        raise ValueError("step 必须为正数")

    near = float(ray["near"])
    far = float(ray["far"])
    surface_t = ray.get("surface_t")
    cutoff = min(far, float(surface_t)) if surface_t is not None else far

    transmit = 1.0
    scatter = 0.0
    steps = 0
    skipped = 0
    records = []

    if cutoff > near:
        intervals = _build_intervals(ray, near, cutoff)
        for lo, hi, block, is_empty in intervals:
            if is_empty and skip_empty:
                # 整块空区一次越过，不产生任何积分子段
                skipped += 1
                if trace:
                    records.append(("skip", lo, hi))
                continue

            position = lo
            while position < hi:
                next_position = min(position + step, hi)
                if not is_empty:
                    transmit, scatter = _integrate_segment(
                        transmit, scatter, block, position, next_position)
                    if trace:
                        records.append(("dense", position, next_position))
                elif trace:
                    records.append(("empty", position, next_position))
                steps += 1
                position = next_position

    surface_color = float(ray["surface_color"])
    result = {
        "transmit": transmit,
        "scatter": scatter,
        "color": scatter + transmit * surface_color,
        "steps": steps,
        "skipped": skipped,
    }
    if trace:
        result["trace"] = records
    return result


def integrate_scene(scene, skip_empty):
    step = float(scene["step"])
    return [integrate_ray(ray, step, skip_empty) for ray in scene["rays"]]


def main():
    parser = argparse.ArgumentParser(description="离线体积雾沿视线积分")
    parser.add_argument("scene", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--no-skip", action="store_true",
                        help="关闭空块跳过，空区也按 step 分段积分")
    args = parser.parse_args()

    scene = json.loads(args.scene.read_text())
    rays = integrate_scene(scene, skip_empty=not args.no_skip)
    payload = {"rays": [dict((k, v) for k, v in ray.items() if k != "trace")
                        for ray in rays]}
    args.output.write_text(json.dumps(payload, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
