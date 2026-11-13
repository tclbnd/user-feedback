"""多投影曲面屏校准与边缘融合（纯标准库实现，Python 3.9+）。

处理流水线：
1. 几何标定：对每台投影的标定格点，在统一曲面坐标 (u,v) 与屏幕像素
   (x,y) 之间拟合二次多项式映射（基 1,u,v,u2,uv,v2），输出重投影 RMS。
2. 有效区域/遮挡：blocked 像素与指向曲面网格之外的像素权重为零；
   对每个有效像素做曲面栅格落栅，按到该投影有效矩形边界的距离+1
   生成初始融合权重，并在同一曲面点上跨投影归一化。
3. 线性亮度补偿：在解码后的线性域补偿每台投影的增益 gain、伽马 gamma
   与黑位 black，输出可直接播放的电信号 signal、融合权重 weights，
   以及按曲面网格相加得到的实际亮度 combined 与校准报告。

单投影（无重叠）时权重恒为 1，曲面网格每个点仅由一台投影负责，
从而保留原始渲染目标，不做任何混合改写。

CLI: python3 projection.py input.json output.json
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

BASIS_SIZE = 6
_DIGITS = 10


# --------------------------------------------------------------------------
# 基础数值工具（纯标准库最小二乘：Householder QR + 上三角回代）
# --------------------------------------------------------------------------
def poly_basis(u, v):
    """二次多项式基 (1, u, v, u*u, u*v, v*v)。"""
    u = float(u)
    v = float(v)
    return [1.0, u, v, u * u, u * v, v * v]


def _solve_upper(upper, rhs):
    """回代解上三角方程组 U x = b。"""
    k = len(upper)
    out = [0.0] * k
    for i in range(k - 1, -1, -1):
        value = rhs[i] - sum(upper[i][j] * out[j] for j in range(i + 1, k))
        out[i] = value / upper[i][i]
    return out


def _qr_least_squares(rows, target, tol=1e-12):
    """Householder QR 求解满秩最小二乘问题 min ||A x - b||。

    rows 为 m 行 n 列设计矩阵（m >= n），target 为长度 m 的观测。
    返回 (系数向量, 数值秩)。秩不足时抛 ValueError。
    """
    m = len(rows)
    n = len(rows[0])
    work = [list(map(float, rows[i])) + [float(target[i])] for i in range(m)]
    scale = max((abs(work[i][j]) for i in range(m) for j in range(n)),
                default=0.0)
    diag_tol = tol * max(1.0, scale)
    rank = 0
    for k in range(n):
        # 对第 k 列的尾部构造 Householder 向量
        alpha = math.sqrt(sum(work[i][k] ** 2 for i in range(k, m)))
        if alpha <= diag_tol:
            raise ValueError(
                "degenerate calibration model: design matrix rank %d < %d"
                % (rank, n))
        if work[k][k] >= 0.0:
            alpha = -alpha
        v_k = work[k][k] - alpha
        work[k][k] = alpha
        for i in range(k + 1, m):
            work[i][k] = work[i][k] / v_k  # 下三角部分暂存 Householder 向量
        h_vec = [0.0] * k + [1.0] + [work[i][k] for i in range(k + 1, m)]
        coeff = 2.0 / sum(h * h for h in h_vec)
        # 对剩余列与右端项施加反射 H = I - tau v v^T
        for j in range(k + 1, n + 1):
            dot = sum(h_vec[i] * work[i][j] for i in range(k, m))
            tau = coeff * dot
            for i in range(k, m):
                work[i][j] -= tau * h_vec[i]
        rank += 1
    upper = [[work[i][j] if i <= j else 0.0 for j in range(n)]
             for i in range(n)]
    rhs = [work[i][n] for i in range(n)]
    return _solve_upper(upper, rhs), rank


def fit_polynomial(markers):
    """由 markers（surface=[u,v], screen=[x,y]）拟合 x、y 两个二次模型。

    返回 (coeff_x, coeff_y, rms)。有效点不足或设计矩阵秩亏时抛 ValueError。
    """
    if len(markers) < BASIS_SIZE:
        raise ValueError(
            "degenerate calibration model: need >= %d markers, got %d"
            % (BASIS_SIZE, len(markers)))
    rows = [poly_basis(m["surface"][0], m["surface"][1]) for m in markers]
    sx = [float(m["screen"][0]) for m in markers]
    sy = [float(m["screen"][1]) for m in markers]
    coeff_x, rank_x = _qr_least_squares(rows, sx)
    coeff_y, rank_y = _qr_least_squares(rows, sy)
    sq_sum = 0.0
    for row, marker in zip(rows, markers):
        px = sum(c * b for c, b in zip(coeff_x, row))
        py = sum(c * b for c, b in zip(coeff_y, row))
        dx = px - float(marker["screen"][0])
        dy = py - float(marker["screen"][1])
        sq_sum += dx * dx + dy * dy
    rms = math.sqrt(sq_sum / len(markers))
    return coeff_x, coeff_y, rms


def eval_poly(coeff, u, v):
    return sum(c * b for c, b in zip(coeff, poly_basis(u, v)))


# --------------------------------------------------------------------------
# 栅格距离变换：有效像素到有效矩形边界的曼哈顿距离（两趟 chamfer）
# --------------------------------------------------------------------------
def boundary_distance(valid):
    """对二维布尔数组 valid，返回每个 True 像素到最近 False 像素的距离+1。

    False 位置返回 0，边界像素距离为 1。
    """
    h = len(valid)
    w = len(valid[0]) if h else 0
    inf = float("inf")
    dist = [[0.0 if valid[y][x] else inf for x in range(w)] for y in range(h)]
    for y in range(h):  # 前向趟：上、左
        for x in range(w):
            if dist[y][x] == inf:
                continue
            if y > 0 and dist[y - 1][x] + 1.0 < dist[y][x]:
                dist[y][x] = dist[y - 1][x] + 1.0
            if x > 0 and dist[y][x - 1] + 1.0 < dist[y][x]:
                dist[y][x] = dist[y][x - 1] + 1.0
    for y in range(h - 1, -1, -1):  # 后向趟：下、右
        for x in range(w - 1, -1, -1):
            if dist[y][x] == inf:
                continue
            if y + 1 < h and dist[y + 1][x] + 1.0 < dist[y][x]:
                dist[y][x] = dist[y + 1][x] + 1.0
            if x + 1 < w and dist[y][x + 1] + 1.0 < dist[y][x]:
                dist[y][x] = dist[y][x + 1]
    out = [[0.0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            out[y][x] = 0.0 if dist[y][x] == inf else dist[y][x] + 1.0
    return out


def bilinear_sample(grid, u, v):
    """在尺寸 H x W 的曲面网格 grid 上按浮点 (u 列, v 行) 双线性采样。

    坐标越界返回 None。
    """
    h = len(grid)
    w = len(grid[0])
    if u < 0.0 or v < 0.0 or u > w - 1.0 or v > h - 1.0:
        return None
    u0 = int(math.floor(u))
    v0 = int(math.floor(v))
    u1 = min(u0 + 1, w - 1)
    v1 = min(v0 + 1, h - 1)
    fu = u - u0
    fv = v - v0
    return (grid[v0][u0] * (1.0 - fu) * (1.0 - fv)
            + grid[v0][u1] * fu * (1.0 - fv)
            + grid[v1][u0] * (1.0 - fu) * fv
            + grid[v1][u1] * fu * fv)


def _round(value):
    return round(float(value), _DIGITS)


# --------------------------------------------------------------------------
# 主流水线
# --------------------------------------------------------------------------
def compose(data):
    """根据输入场景 JSON 生成每台投影的播放图、权重、合成亮度与校准报告。"""
    pattern = data["pattern"]
    grid_h = len(pattern)
    grid_w = len(pattern[0]) if grid_h else 0
    if grid_h == 0 or grid_w == 0:
        raise ValueError("empty pattern")
    projectors = data["projectors"]
    if not projectors:
        raise ValueError("no projectors")

    # --- 1. 几何标定 -----------------------------------------------------
    calibration = []
    for projector in projectors:
        coeff_x, coeff_y, rms = fit_polynomial(projector["markers"])
        calibration.append({
            "id": projector["id"],
            "x": [_round(c) for c in coeff_x],
            "y": [_round(c) for c in coeff_y],
            "rms": _round(rms),
        })
    calibration.sort(key=lambda item: item["id"])

    # --- 2. 落栅与融合权重 ----------------------------------------------
    # contributions[gv][gu] 记录落在该曲面格点的所有 (投影序号, 像素序号, 原始权重)
    contributions = [[[] for _ in range(grid_w)] for _ in range(grid_h)]
    views = []
    for pi, projector in enumerate(projectors):
        pixels = projector["pixels"]
        blocked = projector.get("blocked")
        h = len(pixels)
        w = len(pixels[0]) if h else 0
        if blocked is not None and (len(blocked) != h or
                                    any(len(row) != w for row in blocked)):
            raise ValueError("blocked mask shape mismatch on " + projector["id"])
        valid = [[True] * w for _ in range(h)]
        if blocked is not None:
            for y in range(h):
                for x in range(w):
                    if blocked[y][x]:
                        valid[y][x] = False
        distance = boundary_distance(valid)
        weights_out = [[0.0] * w for _ in range(h)]
        records = [None] * (h * w)
        for y in range(h):
            for x in range(w):
                if not valid[y][x]:
                    records[y * w + x] = (None, 0.0)
                    continue
                u = float(pixels[y][x][0])
                v = float(pixels[y][x][1])
                gu = int(math.floor(u + 0.5))
                gv = int(math.floor(v + 0.5))
                if not (0 <= gu < grid_w and 0 <= gv < grid_h):
                    # 指向曲面网格之外：无效覆盖，权重为零
                    records[y * w + x] = (None, 0.0)
                    continue
                raw = distance[y][x]
                records[y * w + x] = ((gv, gu), raw)
                contributions[gv][gu].append((pi, y * w + x, raw))
        views.append({
            "id": projector["id"],
            "h": h,
            "w": w,
            "pixels": pixels,
            "records": records,
            "weights": weights_out,
            "black": float(projector.get("black", 0.0)),
            "gain": float(projector.get("gain", 1.0)),
            "gamma": float(projector.get("gamma", 1.0)),
            "quantize": bool(projector.get("quantize", False)),
        })

    # 同一曲面点归一化；无活动覆盖的点标记 invalid
    invalid_cells = []
    coverage_count = [[0] * grid_w for _ in range(grid_h)]
    for gv in range(grid_h):
        for gu in range(grid_w):
            entries = contributions[gv][gu]
            if not entries:
                invalid_cells.append((gu, gv))
                continue
            coverage_count[gv][gu] = len(entries)
            total_raw = sum(raw for _, _, raw in entries)
            for pi, pixel_index, raw in entries:
                if total_raw > 0.0:
                    norm = raw / total_raw
                else:
                    norm = 1.0 / len(entries)
                view = views[pi]
                yy, xx = divmod(pixel_index, view["w"])
                view["weights"][yy][xx] = norm

    # --- 3. 线性亮度域增益/伽马/黑位补偿 --------------------------------
    # 活动投影 = 对该曲面点有有效覆盖的投影；B = 活动黑位之和
    active_black = [[0.0] * grid_w for _ in range(grid_h)]
    for gv in range(grid_h):
        for gu in range(grid_w):
            active_black[gv][gu] = sum(
                views[pi]["black"] for pi, _, _ in contributions[gv][gu])

    signals = [[[0.0] * view["w"] for _ in range(view["h"])] for view in views]
    combined = [[0.0] * grid_w for _ in range(grid_h)]
    for gv in range(grid_h):
        for gu in range(grid_w):
            entries = contributions[gv][gu]
            if not entries:
                combined[gv][gu] = None
                continue
            target = float(pattern[gv][gu])
            residual = max(0.0, target - active_black[gv][gu])
            cell_light = 0.0
            for pi, pixel_index, _ in entries:
                view = views[pi]
                yy, xx = divmod(pixel_index, view["w"])
                weight = view["weights"][yy][xx]
                light = view["black"] + weight * residual
                # 电信号：先扣除黑位与增益，再做反伽马
                level = (light - view["black"]) / view["gain"]
                level = min(1.0, max(0.0, level))
                signal = level ** (1.0 / view["gamma"])
                if view["quantize"]:
                    signal = min(1.0,
                                 max(0.0, round(signal * 255.0) / 255.0))
                signals[pi][yy][xx] = signal
                # 投影实际亮度（正向响应链）
                cell_light += view["black"] + view["gain"] * signal ** view["gamma"]
            combined[gv][gu] = cell_light

    output_projectors = []
    for pi, view in enumerate(views):
        output_projectors.append({
            "id": view["id"],
            "signal": [[_round(signals[pi][y][x]) for x in range(view["w"])]
                       for y in range(view["h"])],
            "weights": [[_round(view["weights"][y][x])
                         for x in range(view["w"])]
                        for y in range(view["h"])],
        })

    report = _build_report(views, calibration, coverage_count,
                           invalid_cells, grid_w, grid_h)
    return {
        "projectors": output_projectors,
        "combined": combined,
        "calibration": calibration,
        "report": report,
    }


def _build_report(views, calibration, coverage_count,
                  invalid_cells, grid_w, grid_h):
    total_cells = grid_w * grid_h
    covered = total_cells - len(invalid_cells)
    overlap_cells = sum(1 for row in coverage_count for c in row if c >= 2)
    single_cells = sum(1 for row in coverage_count for c in row if c == 1)
    per_projector = []
    for view in views:
        blocked_pixels = sum(
            1 for cell, _ in view["records"] if cell is None)
        active_pixels = view["h"] * view["w"] - blocked_pixels
        per_projector.append({
            "id": view["id"],
            "shape": [view["h"], view["w"]],
            "black": _round(view["black"]),
            "gain": _round(view["gain"]),
            "gamma": _round(view["gamma"]),
            "quantized": view["quantize"],
            "active_pixels": active_pixels,
            "blocked_pixels": blocked_pixels,
        })
    return {
        "surface_shape": [grid_h, grid_w],
        "covered_cells": covered,
        "invalid_cells": [[gu, gv] for gu, gv in invalid_cells],
        "single_coverage_cells": single_cells,
        "overlap_cells": overlap_cells,
        "single_projector_fallback": overlap_cells == 0 and covered > 0,
        "projectors": per_projector,
        "calibration": calibration,
    }


def add_digest(payload):
    """对确定性序列化后的结果计算 SHA-256 摘要，写回 report.digest。"""
    report = payload["report"]
    canonical = json.dumps(
        {"projectors": payload["projectors"],
         "combined": payload["combined"],
         "calibration": payload["calibration"]},
        sort_keys=True, allow_nan=False, separators=(",", ":"))
    report["digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def main():
    parser = argparse.ArgumentParser(
        description="多投影曲面屏几何标定、融合遮罩与亮度补偿")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--digest", action="store_true",
                        help="在校准报告中附加输出内容的 SHA-256 摘要")
    args = parser.parse_args()
    try:
        payload = compose(json.loads(args.input.read_text()))
        if args.digest:
            add_digest(payload)
        args.output.write_text(
            json.dumps(payload, sort_keys=True, allow_nan=False) + "\n")
    except (ValueError, KeyError, IndexError, TypeError) as err:
        parser.exit(2, str(err) + "\n")


if __name__ == "__main__":
    main()
