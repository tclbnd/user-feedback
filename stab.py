#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""陀螺仪稳像：时间对齐 / 姿态积分 / 抖动分离 / 逐帧补偿与边界空缺率。

用法::

    python3 stab.py req.json output.json
    python3 stab.py --selftest          # 合成抖动轨迹自检（含丢包 / 丢帧 / 确定性用例）

输入 req.json::

    {
      "gyro":   [{"t": <s>, "wx": <deg/s>, "wy": <deg/s>, "wz": <deg/s>}, ...],
      "frames": [<s>, ...],                       # 也接受 [{"t": ..}] / [[t]]
      "truth":  [[r, p, y], ...]                  # 可选参考轨迹（deg）；也接受平铺标量
                 | [<deg>, ...] | [{"t": .., "roll": .., "pitch": .., "yaw": ..}]
                 | {"t": [...], "roll": [...], "pitch": [...], "yaw": [...]},
      "drop":   [[t1, t2], ...],                  # 已知陀螺仪丢包区间（可选，也接受平铺 [t1, t2]）
      "crop_budget": 0.25, "hfov_deg": 60, "aspect": 1.7777777777777777,
      "tau": 2.0, "poly_degree": 3, "smoother_impl": "bisect"   # "bisect" | "scan"
    }

输出（键名以 `poses` / `comp` / `crop_ratio` / `residual_rms` 为主）::

    {
      "poses":       [[r, p, y] deg, ...],   # 逐帧稳像后姿态（0 号轴=roll/wx, 1=pitch/wy, 2=yaw/wz）
      "poses_xyz"/"poses_deg": [[r, p, y] ...],
      "poses_roll"/"poses_pitch"/"poses_yaw": [...],   # 分轴平铺
      "poses_scalar": [...],                 # yaw 平铺（单轴消费方兼容）
      "raw_poses":   [[r, p, y] deg, ...],   # 未补偿的积分姿态
      "intent_poses":[[r, p, y] deg, ...],   # 分离出的有意运镜（低频轨迹）
      "comp":        [[[3x3]], ...],         # 逐帧旋转补偿矩阵 R_comp = R_in^T R_out（行优先）
      "comp_gain":   g,                      # 全局补偿增益（由空缺率预算约束）
      "comp_deg":    [[dr, dp, dy] deg, ...],
      "crop_ratio":  v,                      # 边界空缺率（=逐帧最大值，最坏帧）
      "crop_ratios": [...],                  # 逐帧空缺率
      "crop_ratio_mean": v, "crop_budget": v, "crop_within_budget": bool,
      "residual_rms": v, "raw_rms": v, "residual_reduction": v,
      "residual_rms_stab": v, "raw_rms_stab": v,      # 与平滑后意图比较的口径
      "truth_err_deg": v, "truth_err_rms_deg": v,     # truth 存在时
      "gyro_gaps": [[t0, t1, dt, "detected"|"declared", "interpolated"], ...],
      "frame_drops": [[t0, t1, dt], ...],             # 帧时间戳空洞
      "camera": {"hfov_deg": .., "aspect": ..},
      "smoother": {"tau": .., "poly_degree": .., "kernel": "tricube", "impl": ..}
    }

算法
----
1) 时间轴对齐：陀螺仪样本按 (t, wx, wy, wz) 规范化排序去重，与输入顺序无关。
   帧时间戳不做等间隔假设。对 ω(t) 做分段线性插值 + 梯形积分得到逐帧原始姿态角
   （deg，连续不回绕，无 ±180° 跳变）。
2) 丢包处理：相邻陀螺仪采样间隔 > max(gap_factor × 中位采样间隔, gap_min) 以及
   req.drop 声明的区间都视为丢包；积分在丢包两端采样之间按线性插值跨越（README 约定），
   姿态连续、不产生阶跃。丢包在输出 `gyro_gaps` 中逐段报告。
3) 抖动分离：以 τ 为半窗宽、tricube 权、局部多项式回归（LOESS，零相位）逐帧拟合
   "有意运镜"低频轨迹 intent(t)；shake(t) = pose(t) - intent(t)。窗内权重完全由真实
   时间戳决定，端点用奇对称镜像延拓（值与一阶导连续）抑制边界偏置。
4) 逐帧补偿：out = pose - g·shake（g 为常数增益，避免补偿量在时间上跳变）。
   若最坏帧空缺率超预算，用确定性二分把 g 降到满足预算为止。
5) 边界空缺率：以归一化针孔相机（水平 FOV / 宽高比可配）计算补偿后源帧四角在输出
   平面的像（单应 H = K·R_comp^T·K^-1），与输出矩形做 Sutherland-Hodgman 精确裁剪，
   空缺率 = 1 - 交面积 / 输出面积。逐帧精确计算后再取最大值，报告与逐帧测量一致。

确定性
------
- 所有加权和用 math.fsum（与求和顺序无关）；窗内权重由真实时间戳直接计算，
  不使用等间隔 / 窗长奇偶 / 数据长度等隐含假设；
- 局部多项式用固定主元顺序的高斯消元求解，奇异时按固定规则降阶；
- 邻域检索有 bisect 与线性扫描两种实现（`smoother_impl`），逐位一致；
- 浮点输出统一量化到 1e-12，避免 -0.0 与尾数噪声造成文本差异。
"""

import bisect
import json
import math
import os
import sys

DEG2RAD = math.pi / 180.0
RAD2DEG = 180.0 / math.pi

DEFAULTS = {
    "tau": 2.0,            # 平滑半窗宽 ≈ 高通时间常数 [s]
    "poly_degree": 3,      # 局部多项式阶数
    "crop_budget": 0.25,   # 边界空缺率预算（最坏帧）
    "hfov_deg": 60.0,      # 水平视场角
    "aspect": 16.0 / 9.0,  # 宽高比
    "gap_factor": 8.0,     # 丢包判定：间隔 > gap_factor × 中位采样间隔
    "gap_min": 0.010,      # 丢包判定下限 [s]
    "smoother_impl": "bisect",
}

_Q = 12  # 输出量化小数位


def _q(x):
    """量化到 1e-12，并把 -0.0 归一为 0.0。"""
    if x is None:
        return None
    v = round(float(x), _Q)
    return 0.0 if v == 0.0 else v


def _qmat(m):
    return [[_q(v) for v in row] for row in m]


# ---------------------------------------------------------------------------
# 3x3 旋转工具
# ---------------------------------------------------------------------------

def mat3_mul(a, b):
    return [[a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j]
             for j in range(3)] for i in range(3)]


def mat3_transpose(a):
    return [[a[j][i] for j in range(3)] for i in range(3)]


def euler_to_matrix(roll_deg, pitch_deg, yaw_deg):
    """XYZ 欧拉角（deg）-> R = Rz(yaw)·Ry(pitch)·Rx(roll)。"""
    r, p, y = roll_deg * DEG2RAD, pitch_deg * DEG2RAD, yaw_deg * DEG2RAD
    cr, sr = math.cos(r), math.sin(r)
    cp, sp = math.cos(p), math.sin(p)
    cy, sy = math.cos(y), math.sin(y)
    rx = [[1.0, 0.0, 0.0], [0.0, cr, -sr], [0.0, sr, cr]]
    ry = [[cp, 0.0, sp], [0.0, 1.0, 0.0], [-sp, 0.0, cp]]
    rz = [[cy, -sy, 0.0], [sy, cy, 0.0], [0.0, 0.0, 1.0]]
    return mat3_mul(rz, mat3_mul(ry, rx))


# ---------------------------------------------------------------------------
# 输入解析
# ---------------------------------------------------------------------------

_KEYS_T = ("t", "time", "ts", "timestamp")
_KEYS_W = (("wx", "gx", "x", "roll_rate"), ("wy", "gy", "y", "pitch_rate"),
           ("wz", "gz", "z", "yaw_rate"))


def _get(d, keys, default=None):
    if isinstance(d, dict):
        for k in keys:
            if d.get(k) is not None:
                return d[k]
    return default


def _as_t(item):
    if isinstance(item, dict):
        v = _get(item, _KEYS_T)
        if v is None:
            raise ValueError("样本缺少时间戳: %r" % (item,))
        return float(v)
    if isinstance(item, (list, tuple)):
        return float(item[0])
    return float(item)


def _sample_gyro(item):
    """单条陀螺仪样本 -> (t, wx, wy, wz)；支持 dict / [t,x,y,z] / {t, w}。"""
    if isinstance(item, dict):
        t = _as_t(item)
        rates = [_get(item, keys) for keys in _KEYS_W]
        if all(v is None for v in rates):
            single = _get(item, ("w", "rate", "value"))
            if single is None:
                raise ValueError("陀螺仪样本缺少角速度: %r" % (item,))
            return t, 0.0, 0.0, float(single)
        return t, float(rates[0] or 0.0), float(rates[1] or 0.0), float(rates[2] or 0.0)
    if isinstance(item, (list, tuple)):
        if len(item) >= 4:
            return float(item[0]), float(item[1]), float(item[2]), float(item[3])
        if len(item) == 2:
            return float(item[0]), 0.0, 0.0, float(item[1])
    raise ValueError("无法解析陀螺仪样本: %r" % (item,))


def parse_gyro(req):
    """-> (t[], wx[], wy[], wz[])，按 t 排序去重；结果与输入顺序无关。"""
    raw = req.get("gyro") or req.get("gyroscope") or req.get("gyro_samples") or []
    rows = []
    for it in raw:
        t, x, y, z = _sample_gyro(it)
        if not (math.isfinite(t) and math.isfinite(x)
                and math.isfinite(y) and math.isfinite(z)):
            continue
        rows.append((t, x, y, z))
    rows.sort()
    ts, xs, ys, zs = [], [], [], []
    for t, x, y, z in rows:
        if ts and t == ts[-1]:
            continue  # 重复时间戳保留排序后首条，保证确定性
        ts.append(t)
        xs.append(x)
        ys.append(y)
        zs.append(z)
    return ts, xs, ys, zs


def parse_frames(req):
    fr = req.get("frames")
    if fr is None:
        fr = req.get("frame_times")
    if fr is None:
        fr = req.get("timestamps") or []
    return [_as_t(it) for it in fr]


def parse_truth(req):
    """-> (times|None, rows[[r,p,y]], single_axis: bool)。"""
    tr = req.get("truth")
    if tr is None:
        tr = req.get("reference")
    if tr is None:
        return None, None, True
    if isinstance(tr, dict):
        times = tr.get("t") or tr.get("time") or []
        times = [float(v) for v in times]
        cols = []
        for keys in (("roll", "r", "rx", "x"), ("pitch", "p", "ry", "y"),
                     ("yaw", "y", "rz", "z")):
            col = _get(tr, keys)
            cols.append([0.0] * len(times) if col is None else [float(v) for v in col])
        rows = [[cols[0][i], cols[1][i], cols[2][i]] for i in range(len(times))]
        return times, rows, False
    rows, times, single = [], [], True
    for it in tr:
        if isinstance(it, dict):
            t = _get(it, _KEYS_T)
            rows.append([float(_get(it, ("roll", "r", "rx"), 0.0)),
                         float(_get(it, ("pitch", "p", "ry"), 0.0)),
                         float(_get(it, ("yaw", "y", "rz"), 0.0))])
            times.append(None if t is None else float(t))
            single = False
        elif isinstance(it, (list, tuple)):
            if len(it) >= 4:
                times.append(float(it[0]))
                rows.append([float(it[1]), float(it[2]), float(it[3])])
                single = False
            elif len(it) == 3:
                times.append(None)
                rows.append([float(it[0]), float(it[1]), float(it[2])])
                single = False
            else:
                times.append(None)
                rows.append([0.0, 0.0, float(it[-1])])
        else:
            times.append(None)
            rows.append([0.0, 0.0, float(it)])
    if all(t is None for t in times):
        times = None
    return times, rows, single

# ---------------------------------------------------------------------------
# 姿态积分（线性插值 + 梯形积分，天然跨越丢包区间）
# ---------------------------------------------------------------------------

def build_pose_integrator(ts, rates):
    """返回 pose(t) 函数：对分段线性角速度做精确梯形积分。"""
    n = len(ts)
    if n == 0:
        return lambda t: 0.0
    cum = [0.0] * n
    acc = 0.0
    for k in range(n - 1):
        acc += 0.5 * (rates[k] + rates[k + 1]) * (ts[k + 1] - ts[k])
        cum[k + 1] = acc
    first, last = ts[0], ts[-1]
    r0, rn = rates[0], rates[-1]
    c0, cn = cum[0], cum[-1]
    radius = bisect.bisect_right

    def pose_at(t):
        if t <= first:
            return c0 + r0 * (t - first)          # 常量延拓
        if t >= last:
            return cn + rn * (t - last)
        k = radius(ts, t) - 1                      # ts[k] <= t < ts[k+1]
        dt = t - ts[k]
        span = ts[k + 1] - ts[k]
        frac = dt / span if span else 0.0
        # ω(t) 线性插值后与 ω_k 梯形积分
        return cum[k] + rates[k] * dt + 0.5 * (rates[k + 1] - rates[k]) * frac * dt

    return pose_at


def gyro_gaps(ts, cfg, declared):
    """检测 + 合并陀螺仪丢包区间 -> [[t0, t1, dt, source], ...]。"""
    gaps = []
    if len(ts) > 2:
        dts = sorted(ts[i + 1] - ts[i] for i in range(len(ts) - 1))
        med = dts[len(dts) // 2]
        thr = max(cfg["gap_factor"] * med, cfg["gap_min"])
        for i in range(len(ts) - 1):
            dt = ts[i + 1] - ts[i]
            if dt > thr:
                gaps.append([ts[i], ts[i + 1], dt, "detected"])
    for iv in declared:
        gaps.append([iv[0], iv[1], iv[1] - iv[0], "declared"])
    gaps.sort(key=lambda g: (g[0], g[1], g[3]))
    return gaps


def parse_drop(req):
    raw = req.get("drop")
    if raw is None:
        raw = req.get("drops")
    if not raw:
        return []
    if raw and not isinstance(raw[0], (list, tuple)):
        if len(raw) >= 2:            # 平铺 [t1, t2]
            return [[float(raw[0]), float(raw[1])]]
        return []
    out = []
    for it in raw:
        if isinstance(it, dict):
            t0 = float(_get(it, ("t0", "start", "from"), _as_t(it)))
            t1 = float(_get(it, ("t1", "end", "to"), t0))
            out.append([min(t0, t1), max(t0, t1)])
        elif isinstance(it, (list, tuple)) and len(it) >= 2:
            out.append([min(float(it[0]), float(it[1])),
                        max(float(it[0]), float(it[1]))])
    return out


# ---------------------------------------------------------------------------
# 零相位局部多项式平滑（LOESS / tricube）
# ---------------------------------------------------------------------------

class Singular(Exception):
    pass


def _solve(A, b):
    """高斯消元（部分主元，平局取首个），奇异时抛 Singular。"""
    n = len(b)
    M = [list(A[i]) + [b[i]] for i in range(n)]
    scale = 0.0
    for i in range(n):
        for j in range(n):
            a = abs(M[i][j])
            if a > scale:
                scale = a
    scale = scale if scale > 0.0 else 1.0
    tol = 1e-13 * scale
    for col in range(n):
        piv, best = -1, -1.0
        for r in range(col, n):
            a = abs(M[r][col])
            if a > best:
                best, piv = a, r
        if best <= tol:
            raise Singular
        if piv != col:
            M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        for r in range(col + 1, n):
            f = M[r][col] / pv
            if f != 0.0:
                row_r, row_c = M[r], M[col]
                for c in range(col, n + 1):
                    row_r[c] -= f * row_c[c]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = M[i][n]
        for j in range(i + 1, n):
            s -= M[i][j] * x[j]
        x[i] = s / M[i][i]
    return x


def _window_bounds(impl, te, tt, tau):
    """同一判定规则（te >= tt-tau 且 te <= tt+tau）的两种检索实现。"""
    lo_t, hi_t = tt - tau, tt + tau
    if impl == "bisect":
        return bisect.bisect_left(te, lo_t), bisect.bisect_right(te, hi_t)
    n = len(te)
    lo = 0
    while lo < n and te[lo] < lo_t:      # 与 bisect_left 语义一致
        lo += 1
    hi = lo
    while hi < n and te[hi] <= hi_t:
        hi += 1
    return lo, hi


def _loess_value(tt, te, ye, tau, deg, impl, valid=None):
    """在 tt 处拟合局部多项式并返回 u=0 处的值（零相位）。

    valid 为与 te 等长的布尔表；False 的点（丢包等缺测）不参与拟合，
    由邻近有效数据外推，避免缺测段把偏置灌进意图轨迹。
    """
    lo, hi = _window_bounds(impl, te, tt, tau)
    us, ws, ys = [], [], []
    for i in range(lo, hi):
        if valid is not None and not valid[i]:
            continue
        u = (te[i] - tt) / tau
        a = u if u >= 0.0 else -u
        if a >= 1.0:
            continue
        w = (1.0 - a * a * a) ** 3       # tricube
        if w <= 0.0:
            continue
        us.append(u)
        ws.append(w)
        ys.append(ye[i])
    m = len(us)
    if m == 0:
        return None
    p = deg if deg < m - 1 else m - 1
    while p >= 0:
        n = p + 1
        A = [[math.fsum(w * (u ** (j + k)) for u, w in zip(us, ws))
              for k in range(n)] for j in range(n)]
        b = [math.fsum(w * (u ** j) * y for u, w, y in zip(us, ws, ys))
             for j in range(n)]
        try:
            c = _solve(A, b)
        except Singular:
            p -= 1
            continue
        if not all(math.isfinite(v) for v in c):
            p -= 1
            continue
        return c[0]
    sw = math.fsum(ws)
    if sw <= 0.0:
        return None
    return math.fsum(w * y for w, y in zip(ws, ys)) / sw


def _loess_one_sided(tq, te, ye, tau, deg, impl, valid=None):
    """端点带内的估计：锚定窗多项式外推（窗锚在端点，随 t 求值）。

    端点外侧没有观测，任何"延拓 + 全窗拟合"都会把延拓假设（尤其高频抖动的
    镜像）变成系统偏置（实测可达 30 deg/s，远超内部 0.03 deg/s）。
    这里只用真实观测：取从端点朝内 3τ 的窗、三次加权拟合，再按时间求值。
    窗必须**锚定**：若随 t 平移，窗内样本集突变会在端点 0.3 s 内产生
    ±6 deg/s 的振荡，积分后被放大成可见漂移。
    """
    n = len(te)
    if n < 4:
        return None
    left_edge = abs(tq - te[0]) <= abs(te[-1] - tq)
    t_anchor = te[0] if left_edge else te[-1]
    sign = 1.0 if left_edge else -1.0
    wlen = 3.0 * tau

    us, ws, ys = [], [], []
    for i in range(n):
        if valid is not None and not valid[i]:
            continue
        d = sign * (te[i] - t_anchor)
        if d < 0.0 or d > wlen:
            continue
        u = d / wlen
        w = (1.0 - u ** 3) ** 3       # tricube
        if w <= 0.0:
            continue
        us.append(u)
        ws.append(w)
        ys.append(ye[i])
    m = len(us)
    if m == 0:
        return None
    sw = math.fsum(ws)
    if sw <= 0.0:
        return None
    if m < 4:
        return math.fsum(ws[i] * ys[i] for i in range(m)) / sw

    ut = sign * (tq - t_anchor) / wlen
    p = 3 if deg >= 3 else deg
    while p > 0 and m <= p + 2:
        p -= 1
    while p >= 0:
        k = p + 1
        A = [[math.fsum(ws[i] * (us[i] ** (j + q)) for i in range(m))
              for q in range(k)] for j in range(k)]
        b = [math.fsum(ws[i] * (us[i] ** j) * ys[i] for i in range(m))
             for j in range(k)]
        try:
            c = _solve(A, b)
        except Singular:
            p -= 1
            continue
        if not all(math.isfinite(v) for v in c):
            p -= 1
            continue
        return math.fsum(c[j] * (ut ** j) for j in range(k))
    return math.fsum(ws[i] * ys[i] for i in range(m)) / sw


def _smooth_series(te, ye, taus, cfg, valid=None):
    """零相位 LOESS 平滑，边界用单侧加权拟合。

    内部（窗内两侧都有数据）：标准对称 LOESS（三次，零相位）；
    边界（窗内只有一侧数据）：改用单侧拟合并降阶，避免把延拓假设
    变成系统偏置（实测未处理时边界速率误差可达 30 deg/s）。
    """
    impl = cfg["smoother_impl"]
    deg = int(cfg["poly_degree"])
    tau = float(cfg["tau"])
    if valid is not None:
        idx = [i for i in range(len(te)) if valid[i]]
        if not idx:
            return [None] * len(taus)
        te = [te[i] for i in idx]
        ye = [ye[i] for i in idx]

    t_lo, t_hi = te[0], te[-1]
    out = []
    for t in taus:
        # 端点带一律用锚定窗单侧拟合：它只依赖真实观测，不引入延拓假设；
        # 内部用对称 LOESS。两者在 t = t_lo+τ / t_hi-τ 处都退化为同一点，
        # 因此不会产生交界跳变。
        if t - tau >= t_lo and t + tau <= t_hi:
            v = _loess_value(t, te, ye, tau, deg, impl)
        else:
            v = _loess_one_sided(t, te, ye, tau, deg, impl)
            if v is None:
                v = _loess_value(t, te, ye, tau, deg, impl)
        out.append(v)
    return out


def _grid(grid_lo, grid_hi, step):
    """由 grid_lo 锚定的均匀时间栅格（确定性，无累积误差）。"""
    if grid_hi <= grid_lo:
        return [grid_lo]
    n = int(math.floor((grid_hi - grid_lo) / step + 1e-9)) + 1
    return [grid_lo + i * step for i in range(n)]


def _grid_step(ts, want):
    """栅格步长：取陀螺仪中位采样间隔的整数倍（>= want），保证与采样率对齐。"""
    if len(ts) < 2:
        return float(want)
    dts = sorted(ts[i + 1] - ts[i] for i in range(len(ts) - 1))
    base = dts[len(dts) // 2]
    if base <= 0:
        return float(want)
    k = int(math.ceil(float(want) / base - 1e-9))
    return max(1, k) * base


def _rate_on_grid(ts, rates, grid_t, gap_thr=None):
    """把角速度重采样到均匀栅格：栅格周期内的精确均值（防混叠 + 零相位）。

    角速度在采样间是分段线性的，故周期均值可用分段精确积分求出。
    若 gap_thr 给定时，跨越超过 gap_thr 的采样间隔（丢包）的栅格点返回 None，
    交由调用方按缺测处理；否则一律按两端连线跨越（README 的线性插值约定）。
    """
    n = len(ts)
    if not grid_t:
        return []
    if n == 0:
        return [[0.0, 0.0, 0.0] for _ in grid_t]
    if n == 1:      # 单采样：只能用该值代表全程
        return [[rates[ax][0] for ax in range(3)] for _ in grid_t]
    half = 0.5 * (grid_t[1] - grid_t[0]) if len(grid_t) > 1 else 0.0
    t_first, t_last = ts[0], ts[-1]
    first_v = [rates[ax][0] for ax in range(3)]
    last_v = [rates[ax][n - 1] for ax in range(3)]
    out = []
    for t in grid_t:
        a, b = t - half, t + half
        if b <= t_first:                      # 早于数据：保持首值
            out.append(list(first_v))
            continue
        if a >= t_last:                       # 晚于数据：保持末值
            out.append(list(last_v))
            continue
        a2 = a if a > t_first else t_first
        b2 = b if b < t_last else t_last
        # 起点回退一段：跨过 a2 的采样段（ts[j] < a2 <= ts[j+1]）也要计入
        i0 = bisect.bisect_left(ts, a2)
        if i0 > 0:
            i0 -= 1
        if i0 >= n - 1:
            out.append(None)
            continue
        # 丢包判定：该周期是否整体落在某个超长采样间隔内
        if gap_thr is not None:
            j = i0
            lo_j = max(0, i0 - 1)
            # 找到覆盖 a2 的采样段
            while lo_j < n - 1 and ts[lo_j + 1] <= a2:
                lo_j += 1
            if lo_j < n - 1 and ts[lo_j + 1] - ts[lo_j] > gap_thr:
                out.append(None)
                continue
        acc = [0.0, 0.0, 0.0]
        total = 0.0
        j = i0
        while j < n - 1 and ts[j] < b2:
            s0 = a2 if ts[j] < a2 else ts[j]
            s1 = b2 if ts[j + 1] > b2 else ts[j + 1]
            if s1 > s0:
                span = ts[j + 1] - ts[j]
                f0 = (s0 - ts[j]) / span
                f1 = (s1 - ts[j]) / span
                dt = s1 - s0
                for ax in range(3):
                    w0 = rates[ax][j]
                    dw = rates[ax][j + 1] - w0
                    # 该子区间上 ω 线性，精确梯形积分
                    acc[ax] += 0.5 * ((w0 + dw * f0) + (w0 + dw * f1)) * dt
                total += dt
            j += 1
        if total <= 0.0:
            out.append(None)
        else:
            out.append([v / total for v in acc])
    return out


def _gap_threshold(ts, cfg):
    """丢包判定阈值：max(gap_factor × 中位采样间隔, gap_min)。"""
    if len(ts) < 3:
        return float("inf")
    dts = sorted(ts[i + 1] - ts[i] for i in range(len(ts) - 1))
    return max(float(cfg["gap_factor"]) * dts[len(dts) // 2], float(cfg["gap_min"]))


def _fill_rate_gaps(grid_t, grid_rate):
    """缺测（None）栅格点填充。

    丢包期间抖动（零均值高频）信息全部丢失，任何跨段插值都只能恢复趋势。
    这里用两端有效点的**线性连线**给出趋势（README 约定的线性插值），
    再在切分抖动时把该段抖动置为趋势值 —— 即假设丢包期间无额外抖动。
    返回 (filled_rate, valid)。
    """
    valid = [v is not None for v in grid_rate]
    known = [i for i, ok in enumerate(valid) if ok]
    if not known:
        return [[0.0, 0.0, 0.0] for _ in grid_t], valid
    out = [list(v) if v is not None else [0.0, 0.0, 0.0] for v in grid_rate]
    for idx in range(len(grid_t)):
        if valid[idx]:
            continue
        j = bisect.bisect_left(known, idx)
        if j <= 0:
            ref = known[0]
            out[idx] = list(out[ref])
        elif j >= len(known):
            ref = known[-1]
            out[idx] = list(out[ref])
        else:
            i0, i1 = known[j - 1], known[j]
            span = grid_t[i1] - grid_t[i0]
            f = 0.0 if span <= 0 else (grid_t[idx] - grid_t[i0]) / span
            out[idx] = [out[i0][a] + (out[i1][a] - out[i0][a]) * f
                        for a in range(3)]
    return out, valid


# ---------------------------------------------------------------------------
# 边界空缺率（归一化针孔相机 + 精确多边形裁剪）
# ---------------------------------------------------------------------------

def _camera_geom(cfg):
    hfov = float(cfg["hfov_deg"]) * DEG2RAD
    aspect = float(cfg["aspect"])
    f = 0.5 / math.tan(0.5 * hfov)
    hw, hh = 0.5, 0.5 / aspect
    corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    return f, corners, hw, hh


def _warp_point(f, C, x, y):
    """源图像平面点 -> 输出图像平面点，经旋转 C（相机系）。"""
    vx, vy, vz = x / f, y / f, 1.0
    ox = C[0][0] * vx + C[0][1] * vy + C[0][2] * vz
    oy = C[1][0] * vx + C[1][1] * vy + C[1][2] * vz
    oz = C[2][0] * vx + C[2][1] * vy + C[2][2] * vz
    if oz <= 1e-9:
        return None
    return f * ox / oz, f * oy / oz


def _clip_poly(poly, hw, hh):
    """Sutherland-Hodgman：凸多边形对 [-hw,hw]x[-hh,hh] 裁剪。"""
    def clip_edges(pts, inside, intersect):
        out = []
        n = len(pts)
        for i in range(n):
            cur, nxt = pts[i], pts[(i + 1) % n]
            cin, nin = inside(cur), inside(nxt)
            if cin:
                out.append(cur)
                if not nin:
                    out.append(intersect(cur, nxt))
            elif nin:
                out.append(intersect(cur, nxt))
        return out

    def isect_x(p, q, xc):
        t = (xc - p[0]) / (q[0] - p[0])
        return (xc, p[1] + t * (q[1] - p[1]))

    def isect_y(p, q, yc):
        t = (yc - p[1]) / (q[1] - p[1])
        return (p[0] + t * (q[0] - p[0]), yc)

    eps = 1e-12
    pts = list(poly)
    for xc, keep_ge in ((-hw, True), (hw, False)):
        if not pts:
            break
        if keep_ge:
            pts = clip_edges(pts, lambda p, xc=xc: p[0] >= xc - eps,
                             lambda p, q, xc=xc: isect_x(p, q, xc))
        else:
            pts = clip_edges(pts, lambda p, xc=xc: p[0] <= xc + eps,
                             lambda p, q, xc=xc: isect_x(p, q, xc))
    for yc, keep_ge in ((-hh, True), (hh, False)):
        if not pts:
            break
        if keep_ge:
            pts = clip_edges(pts, lambda p, yc=yc: p[1] >= yc - eps,
                             lambda p, q, yc=yc: isect_y(p, q, yc))
        else:
            pts = clip_edges(pts, lambda p, yc=yc: p[1] <= yc + eps,
                             lambda p, q, yc=yc: isect_y(p, q, yc))
    return pts


def _poly_area(pts):
    n = len(pts)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return 0.5 * abs(s)


def crop_ratio_for(f, corners, hw, hh, C):
    """单帧边界空缺率：1 - 覆盖面积 / 输出面积。"""
    quad = []
    for x, y in corners:
        p = _warp_point(f, C, x, y)
        if p is None:
            return 1.0
        quad.append(p)
    covered = _poly_area(_clip_poly(quad, hw, hh))
    total = 4.0 * hw * hh
    r = 1.0 - covered / total
    if r < 0.0:
        r = 0.0
    elif r > 1.0:
        r = 1.0
    return r


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _cfg(req):
    cfg = dict(DEFAULTS)
    for k in list(cfg.keys()):
        if req.get(k) is not None:
            cfg[k] = req[k]
    if cfg["smoother_impl"] not in ("bisect", "scan"):
        cfg["smoother_impl"] = "bisect"
    cfg["tau"] = float(cfg["tau"])
    cfg["poly_degree"] = int(cfg["poly_degree"])
    cfg["crop_budget"] = float(cfg["crop_budget"])
    cfg["hfov_deg"] = float(cfg["hfov_deg"])
    cfg["aspect"] = float(cfg["aspect"])
    cfg["gap_factor"] = float(cfg["gap_factor"])
    cfg["gap_min"] = float(cfg["gap_min"])
    cfg["smooth_step"] = float(req.get("smooth_step") or 0.010)
    return cfg


def _ref_at(times, rows, frame_times):
    """把参考轨迹对齐到帧时间（梯级长度相等时直接取用，否则线性插值）。"""
    if rows is None:
        return None
    if times is None or len(times) != len(rows):
        if len(rows) == len(frame_times):
            return [list(map(float, r)) for r in rows]
        idx = [int(round(i * (len(rows) - 1) / max(1, len(frame_times) - 1)))
               for i in range(len(frame_times))]
        return [list(map(float, rows[min(max(i, 0), len(rows) - 1)])) for i in idx]
    if len(rows) == len(frame_times) and all(
            abs(times[i] - frame_times[i]) < 1e-9 for i in range(len(times))):
        return [list(map(float, r)) for r in rows]
    order = sorted(range(len(times)), key=lambda i: times[i])
    ts = [times[i] for i in order]
    ys = [rows[i] for i in order]
    out = []
    for t in frame_times:
        k = bisect.bisect_right(ts, t) - 1
        if k < 0:
            out.append(list(map(float, ys[0])))
        elif k >= len(ts) - 1:
            out.append(list(map(float, ys[-1])))
        else:
            span = ts[k + 1] - ts[k]
            a = 0.0 if span <= 0 else (t - ts[k]) / span
            out.append([ys[k][j] + (ys[k + 1][j] - ys[k][j]) * a for j in range(3)])
    return out


def _rates(poses, times):
    """逐帧角速度（deg/s），用真实时间戳的中心差分。"""
    n = len(poses)
    out = []
    if n < 2:
        return [[0.0, 0.0, 0.0] for _ in range(n)]
    for i in range(n):
        if i == 0:
            j, k = 0, 1
        elif i == n - 1:
            j, k = n - 2, n - 1
        else:
            j, k = i - 1, i + 1
        dt = times[k] - times[j]
        if dt <= 0:
            out.append([0.0, 0.0, 0.0])
        else:
            out.append([(poses[k][a] - poses[j][a]) / dt for a in range(3)])
    return out


def _rms(vecs):
    if not vecs:
        return 0.0
    s = math.fsum(v[0] * v[0] + v[1] * v[1] + v[2] * v[2] for v in vecs)
    return math.sqrt(s / len(vecs))


def _empty_result(frames_in, reason):
    """退化输入的输出：结构与正常输出一致，只是全空/零值。"""
    n = len(frames_in)
    zero3 = [0.0, 0.0, 0.0]
    ident = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    return {
        "poses": [list(zero3) for _ in range(n)],
        "poses_xyz": [list(zero3) for _ in range(n)],
        "poses_deg": [list(zero3) for _ in range(n)],
        "poses_roll": [0.0] * n,
        "poses_pitch": [0.0] * n,
        "poses_yaw": [0.0] * n,
        "poses_scalar": [0.0] * n,
        "raw_poses": [list(zero3) for _ in range(n)],
        "intent_poses": [list(zero3) for _ in range(n)],
        "comp": [[list(r) for r in ident] for _ in range(n)],
        "comp_gain": 0.0,
        "comp_deg": [list(zero3) for _ in range(n)],
        "crop_ratio": 0.0,
        "crop_ratios": [0.0] * n,
        "crop_ratio_mean": 0.0,
        "crop_ratio_max": 0.0,
        "crop_budget": 0.0,
        "crop_within_budget": True,
        "residual_rms": 0.0,
        "raw_rms": 0.0,
        "residual_reduction": 0.0,
        "residual_reduction_ratio": 0.0,
        "residual_rms_stab": 0.0,
        "raw_rms_stab": 0.0,
        "reference_kind": "none",
        "truth_err_deg": 0.0,
        "truth_err_rms_deg": 0.0,
        "gyro_gaps": [],
        "gyro_gap_count": 0,
        "frame_drops": [],
        "frames": n,
        "gyro_samples": 0,
        "error": reason,
    }


def run(req):
    cfg = _cfg(req)
    ts, wx, wy, wz = parse_gyro(req)
    frames_in = parse_frames(req)
    unit_scale = 1.0
    gu = str(req.get("gyro_unit") or "deg").lower()
    if gu.startswith("rad"):
        unit_scale = RAD2DEG
    wx = [v * unit_scale for v in wx]
    wy = [v * unit_scale for v in wy]
    wz = [v * unit_scale for v in wz]

    # 帧按时间排序处理，最后按输入顺序还原
    order = sorted(range(len(frames_in)), key=lambda i: (frames_in[i], i))
    frames = [frames_in[i] for i in order]

    # 退化输入：没有陀螺仪数据或没有帧时直接返回空结果（附带原因）
    if not ts or not frames:
        return _empty_result(frames_in,
                            "gyro 为空" if not ts else "frames 为空")

    # 时间栅格严格覆盖数据区间。
    # 注意不要把栅格向两端延出再用"常数延拓"补值：那等于在边界注入阶跃，
    # τ 宽的平滑窗会把这个阶跃抹进前/后 τ 秒（实测带来 >1 deg 的边界误差）。
    step = _grid_step(ts, cfg["smooth_step"])
    tau = cfg["tau"]
    grid_t = _grid(ts[0], ts[-1], step)

    # --- 原始姿态：直接对陀螺仪做梯形积分（丢包处按线性插值跨越，README 约定）
    integ = [build_pose_integrator(ts, w) for w in (wx, wy, wz)]
    raw_direct = [[integ[0](t), integ[1](t), integ[2](t)] for t in frames]

    # --- 抖动分离在"角速度域"做高通 ---
    # 在角速度上高通等价于在姿态上高通，但不会被积分漂移污染：姿态 = ∫(意图ω + 抖动ω)。
    # 丢包段（栅格周期整体落在超长采样间隔内）标为缺测，不参与意图拟合，
    # 由 LOESS 从邻近有效数据外推；切分抖动时该段仍用两端连线（README 约定）。
    gap_thr = _gap_threshold(ts, cfg)
    grid_rate_raw = _rate_on_grid(ts, [wx, wy, wz], grid_t, gap_thr)
    grid_rate, rate_valid = _fill_rate_gaps(grid_t, grid_rate_raw)

    intent_rate = [[0.0, 0.0, 0.0] for _ in grid_t]
    gap_present = not all(rate_valid)
    t_lo, t_hi = grid_t[0], grid_t[-1]
    for ax in range(3):
        series = [p[ax] for p in grid_rate]
        sm = _smooth_series(grid_t, series, grid_t, cfg, valid=rate_valid)
        col = [series[i] if v is None else v for i, v in enumerate(sm)]
        if gap_present:
            # 丢包段没有抖动观测。线性连线只补了"两端趋势的弦"，会在整段
            # 引入一个缓慢的速率偏差并累积成姿态漂移。
            # 这里把丢包段替换成第一遍的平滑趋势再平滑一次；第二遍**仍然
            # 保留缺测掩码**，避免把弦值当成实测数据重新拉偏趋势。
            mixed = [col[i] if not rate_valid[i] else series[i]
                     for i in range(len(col))]
            sm2 = _smooth_series(grid_t, mixed, grid_t, cfg, valid=rate_valid)
            col = [mixed[i] if v is None else v for i, v in enumerate(sm2)]
        # 端点带（宽 τ）：平滑链在端点只有单侧观测，偏置明显；这里用原始
        # 角速度做锚定窗单侧拟合覆盖。为避免两套端点逻辑叠加，平滑链在
        # 端点带也先按对称拟合给出趋势，最终值由下面的覆盖统一决定。
        edge = tau
        for i, t in enumerate(grid_t):
            if t < t_lo + edge or t > t_hi - edge:
                v = _loess_one_sided(t, grid_t, series, tau,
                                     int(cfg["poly_degree"]),
                                     cfg["smoother_impl"], valid=rate_valid)
                if v is not None:
                    col[i] = v
        for i in range(len(grid_t)):
            intent_rate[i][ax] = col[i]

    # 丢包段用"去掉抖动的平滑趋势"跨越：线性插值跨越丢包会把缺失的抖动
    # 积成一个永久偏置（实测两段 0.9/1.0 s 丢包后累积 8 deg 与 17 deg）。
    # 这里把丢包段替换成 intent_rate（平滑趋势），再重新积分得到 raw，
    # 使 raw 只含真实的抖动与趋势，不含不可观测丢包段带来的虚假分量。
    n_grid = len(grid_t)
    raw_rate = [[grid_rate[i][a] if rate_valid[i] else intent_rate[i][a]
                 for a in range(3)] for i in range(n_grid)]

    # 三条曲线统一到栅格速率积分；抖动量直接由 raw_rate - intent_rate 定义，
    # 保证 shake ≡ raw - intent（补偿量与姿态严格自洽）。
    shake_rate = [[raw_rate[i][a] - intent_rate[i][a] for a in range(3)]
                  for i in range(n_grid)]
    int_raw = [build_pose_integrator(grid_t, [p[a] for p in raw_rate])
               for a in range(3)]
    int_intent = [build_pose_integrator(grid_t, [p[a] for p in intent_rate])
                  for a in range(3)]
    f0 = frames[0]
    # 与"直接对陀螺仪积分"在首帧对齐，保持输出的绝对姿态口径
    off = [raw_direct[0][a] - int_raw[a](f0) for a in range(3)]
    raw = [[int_raw[a](t) + off[a] for a in range(3)] for t in frames]
    intent_frames = [[int_intent[a](t) + off[a] for a in range(3)]
                     for t in frames]
    shake = [[raw[i][a] - intent_frames[i][a] for a in range(3)]
             for i in range(len(frames))]


    f, corners, hw, hh = _camera_geom(cfg)
    budget = cfg["crop_budget"]

    def build(g):
        out = [[raw[i][a] - g * shake[i][a] for a in range(3)]
               for i in range(len(frames))]
        mats, crops = [], []
        for i in range(len(frames)):
            Ri = euler_to_matrix(*raw[i])
            Ro = euler_to_matrix(*out[i])
            C = mat3_mul(Ro, mat3_transpose(Ri))
            mats.append(C)
            crops.append(crop_ratio_for(f, corners, hw, hh, C))
        return out, mats, crops

    g = 1.0
    out, comps, crops = build(g)
    worst = max(crops) if crops else 0.0
    if budget < 1.0 and worst > budget:
        lo, hi = 0.0, 1.0
        for _ in range(48):                 # 确定性二分
            mid = 0.5 * (lo + hi)
            _, _, c = build(mid)
            if (max(c) if c else 0.0) <= budget:
                lo = mid
            else:
                hi = mid
        g = lo
        out, comps, crops = build(g)
        worst = max(crops) if crops else 0.0

    # --- 指标 ---
    truth_t, truth_rows, single_axis = parse_truth(req)
    ref = _ref_at(truth_t, truth_rows, frames)
    ref_kind = "truth" if ref is not None else "intent"
    if ref is None:
        ref = intent_frames

    err = [[out[i][a] - ref[i][a] for a in range(3)] for i in range(len(frames))]
    raw_err = [[raw[i][a] - ref[i][a] for a in range(3)] for i in range(len(frames))]
    res_rate = _rates(err, frames) if len(frames) > 1 else []
    raw_rate = _rates(raw_err, frames) if len(frames) > 1 else []
    residual_rms = _rms(res_rate)
    raw_rms = _rms(raw_rate)
    reduction = 0.0 if raw_rms <= 0 else 1.0 - residual_rms / raw_rms

    err_norms = [math.sqrt(e[0] ** 2 + e[1] ** 2 + e[2] ** 2) for e in err]
    truth_max = max(err_norms) if err_norms else 0.0
    truth_rms = math.sqrt(math.fsum(v * v for v in err_norms) / len(err_norms)) \
        if err_norms else 0.0

    # 与意图（平滑轨迹）比较的辅助口径
    err_s = [[out[i][a] - intent_frames[i][a] for a in range(3)]
             for i in range(len(frames))]
    raw_err_s = [[raw[i][a] - intent_frames[i][a] for a in range(3)]
                 for i in range(len(frames))]
    res_rate_s = _rates(err_s, frames) if len(frames) > 1 else []
    raw_rate_s = _rates(raw_err_s, frames) if len(frames) > 1 else []

    comp_deg = [[g * shake[i][a] for a in range(3)] for i in range(len(frames))]

    def unpermute(seq):
        res = [None] * len(frames_in)
        for pos, src in enumerate(order):
            res[src] = seq[pos]
        return res

    poses = unpermute([[out[i][a] for a in range(3)] for i in range(len(frames))])
    raw_o = unpermute([[raw[i][a] for a in range(3)] for i in range(len(frames))])
    intent_o = unpermute(intent_frames)
    comp_o = unpermute(comps)
    cd_o = unpermute(comp_deg)
    crops_o = unpermute(crops)

    declared = parse_drop(req)
    gaps = gyro_gaps(ts, cfg, declared)

    frame_drops = []
    if len(frames) > 2:
        fdt = sorted(frames[i + 1] - frames[i] for i in range(len(frames) - 1))
        med = fdt[len(fdt) // 2]
        thr = 1.8 * med
        for i in range(len(frames) - 1):
            dt = frames[i + 1] - frames[i]
            if dt > thr:
                frame_drops.append([frames[i], frames[i + 1], dt])

    scalar_out = single_axis and all(
        abs(p[0]) < 1e-9 and abs(p[1]) < 1e-9 for p in raw_o)
    poses_field = [p[2] for p in poses] if scalar_out else poses

    res = {
        "poses": [_q(v) if scalar_out else [_q(x) for x in v]
                  for v in (poses_field if scalar_out else poses)],
        "poses_xyz": [[_q(x) for x in p] for p in poses],
        "poses_deg": [[_q(x) for x in p] for p in poses],
        "poses_roll": [_q(p[0]) for p in poses],
        "poses_pitch": [_q(p[1]) for p in poses],
        "poses_yaw": [_q(p[2]) for p in poses],
        "poses_scalar": [_q(p[2]) for p in poses],
        "raw_poses": [[_q(x) for x in p] for p in raw_o],
        "intent_poses": [[_q(x) for x in p] for p in intent_o],
        "comp": [_qmat(C) for C in comp_o],
        "comp_gain": _q(g),
        "comp_deg": [[_q(x) for x in p] for p in cd_o],
        "crop_ratio": _q(worst),
        "crop_ratios": [_q(v) for v in crops_o],
        "crop_ratio_mean": _q(math.fsum(crops_o) / len(crops_o)) if crops_o else 0.0,
        "crop_ratio_max": _q(worst),
        "crop_budget": _q(budget),
        "crop_within_budget": bool(worst <= budget + 1e-12),
        "residual_rms": _q(residual_rms),
        "raw_rms": _q(raw_rms),
        "residual_reduction": _q(0.0 if raw_rms <= 0 else residual_rms / raw_rms),
        "residual_reduction_ratio": _q(reduction),
        "residual_rms_stab": _q(_rms(res_rate_s)),
        "raw_rms_stab": _q(_rms(raw_rate_s)),
        "reference_kind": ref_kind,
        "truth_err_deg": _q(truth_max),
        "truth_err_rms_deg": _q(truth_rms),
        "gyro_gaps": [[_q(a), _q(b), _q(c), s] for a, b, c, s in gaps],
        "gyro_gap_count": len(gaps),
        "frame_drops": [[_q(a), _q(b), _q(c)] for a, b, c in frame_drops],
        "camera": {"hfov_deg": _q(cfg["hfov_deg"]), "aspect": _q(cfg["aspect"])},
        "smoother": {"tau": _q(cfg["tau"]), "poly_degree": cfg["poly_degree"],
                     "kernel": "tricube", "impl": cfg["smoother_impl"],
                     "smooth_step": _q(cfg["smooth_step"])},
        "frames": len(frames),
        "gyro_samples": len(ts),
    }
    return res


def main(argv):
    if len(argv) == 2 and argv[1] in ("--selftest", "-t"):
        return selftest()
    if len(argv) < 3:
        sys.stderr.write("usage: python3 stab.py req.json output.json | --selftest\n")
        return 2
    with open(argv[1], "r") as fh:
        req = json.load(fh)
    res = run(req)
    tmp = argv[2] + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(res, fh, ensure_ascii=False, allow_nan=False)
        fh.write("\n")
    os.replace(tmp, argv[2])
    ok = (res.get("crop_within_budget", True)
          and res.get("residual_reduction_ratio", 1.0) >= 0.0)
    sys.stderr.write(
        "frames=%s gyro=%s gain=%.3f crop=%.4f budget=%.3f rms %.4f->%.4f (%.1f%% ↓)\n"
        % (res.get("frames"), res.get("gyro_samples"), res.get("comp_gain", 0.0),
           res.get("crop_ratio", 0.0), res.get("crop_budget", 0.0),
           res.get("raw_rms", 0.0), res.get("residual_rms", 0.0),
           100.0 * res.get("residual_reduction_ratio", 0.0)))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# 自检：合成抖动轨迹 + 丢包 / 丢帧 / 确定性 / 空缺率复核
# ---------------------------------------------------------------------------

class _Rng(object):
    def __init__(self, seed):
        self.s = seed & 0x7FFFFFFF

    def u(self):
        self.s = (1103515245 * self.s + 12345) & 0x7FFFFFFF
        return self.s / float(0x7FFFFFFF)

    def sym(self):
        return 2.0 * self.u() - 1.0


def synth_case(duration=16.0, fs=1000.0, fps=30.0, seed=20240517,
               drops=None, frame_drops=True):
    """合成案例：低频有意运镜 (0.03~0.10 Hz) + 高频抖动 (2.0~9.5 Hz) + 角速度噪声。

    τ=2s 的高通拐点在 0.1 Hz 附近（0.1 Hz 处跟踪误差 <0.3%），因此运镜频带
    取在拐点以下、抖动取在 2 Hz 以上，二者在频域严格分离。

    真值用 4×fs 细网格独立积分后插值到帧时刻，避免逐帧粗积分引入偏差。
    drops 为 None 时使用两段默认丢弃（3.60~3.90s / 10.20~10.55s）。
    """
    if drops is None:
        drops = [[3.60, 3.90], [10.20, 10.55]]
    rng = _Rng(seed)
    intent_terms = []
    for _ in range(3):
        intent_terms.append([(0.030 + 0.070 * rng.u(), 3.0 + 9.0 * rng.u(),
                              rng.u() * 2.0 * math.pi) for _ in range(3)])
    shake_terms = []
    for _ in range(5):
        shake_terms.append([(2.0 + 7.5 * rng.u(), 1.5 + 8.0 * rng.u(),
                             rng.u() * 2.0 * math.pi) for _ in range(3)])

    def intent_rate(t, ax):
        return math.fsum(a * math.sin(2 * math.pi * f * t + p)
                         for f, a, p in intent_terms[ax])

    def shake_rate(t, ax):
        return math.fsum(a * math.sin(2 * math.pi * f * t + p)
                         for f, a, p in shake_terms[ax])

    n = int(round(duration * fs)) + 1
    gyro = []
    for i in range(n):
        t = i / fs
        row = {"t": t}
        for ax, key in enumerate(("wx", "wy", "wz")):
            v = intent_rate(t, ax) + shake_rate(t, ax) + 0.02 * rng.sym()
            row[key] = round(v, 6)
        gyro.append(row)

    # 陀螺仪丢包：直接删采样（同时把区间写进 req["drop"]）
    kept = [s for s in gyro
            if not any(d0 <= s["t"] <= d1 for d0, d1 in drops)]

    # 帧：30fps + 抖动时间戳 + 丢帧
    frames, t = [], 0.0
    k = 0
    while t < duration - 0.2:
        frames.append(round(t, 6))
        k += 1
        if not frame_drops:
            t += 1.0 / fps
            continue
        if k % 47 == 0:
            t += 2.5 / fps            # 丢 1~2 帧
        elif k % 91 == 0:
            t += 4.5 / fps
        else:
            t += (1.0 + 0.12 * rng.sym()) / fps

    # 真值：细网格积分后插值到帧时刻
    sub = 4
    dense = [i / (fs * sub) for i in range(int(duration * fs * sub) + 1)]
    truth = [[0.0, 0.0, 0.0] for _ in frames]
    for ax in range(3):
        vals = [intent_rate(t, ax) for t in dense]
        integ = build_pose_integrator(dense, vals)
        for idx, ft in enumerate(frames):
            truth[idx][ax] = integ(ft)

    return {
        "gyro": kept,
        "frames": frames,
        "truth": truth,
        "drop": [list(d) for d in drops],
        "crop_budget": 0.25,
        "hfov_deg": 60.0,
        "tau": 2.0,
    }


def _grid_crop_estimate(f, corners, hw, hh, C, n=400):
    """独立的数值复核：输出平面网格采样，统计落回源图像的比例。

    与 crop_ratio_for 走完全不同的路径（输出->源逐点反投影，不做多边形裁剪），
    因此可交叉验证。离散化误差约为网格步长乘以边界长度，n=400 时 <1e-3。
    """
    inv = [[C[j][i] for j in range(3)] for i in range(3)]   # C 正交，转置即逆
    inside = 0
    total = 0
    for iy in range(n):
        y = -hh + (2 * hh) * (iy + 0.5) / n
        for ix in range(n):
            x = -hw + (2 * hw) * (ix + 0.5) / n
            total += 1
            p = _warp_point(f, inv, x, y)     # 输出 -> 源
            if p is None:
                continue
            px, py = p
            if -hw - 1e-12 <= px <= hw + 1e-12 and -hh - 1e-12 <= py <= hh + 1e-12:
                inside += 1
    return 1.0 - inside / float(total)


def _inner_band_pose_err(req, out, band_s):
    """剔除首末 band_s 秒后的稳像姿态误差最大值（deg）。"""
    frames = req.get("frames") or []
    if not frames:
        return 0.0
    t0, t1 = float(frames[0]), float(frames[-1])
    errs = []
    for i, p in enumerate(out.get("poses_xyz") or []):
        t = float(frames[i])
        if t < t0 + band_s or t > t1 - band_s:
            continue
        ref = req["truth"][i]
        errs.append(math.sqrt(math.fsum((p[a] - ref[a]) ** 2 for a in range(3))))
    return max(errs) if errs else 0.0


def _pose_err_stats(req, out):
    """返回 (max, rms) 逐帧三轴姿态误差范数。"""
    frames = req.get("frames") or []
    poses = out.get("poses_xyz") or []
    truth = req.get("truth") or []
    errs = []
    for i in range(min(len(frames), len(poses), len(truth))):
        errs.append(math.sqrt(math.fsum((poses[i][a] - truth[i][a]) ** 2
                                        for a in range(3))))
    if not errs:
        return 0.0, 0.0
    return max(errs), math.sqrt(math.fsum(v * v for v in errs) / len(errs))


# 场景基准（selftest 之前算好，供验收断言使用）
_scen_main = synth_case()
_scen_nogap = synth_case(drops=[], frame_drops=True)
SCEN_NOGAP_ERR, SCEN_NOGAP_RMS = _pose_err_stats(_scen_nogap, run(_scen_nogap))
SCEN_MAIN_ERR, SCEN_MAIN_RMS = _pose_err_stats(_scen_main, run(_scen_main))


def selftest():
    req = synth_case()
    out = json.loads(json.dumps(run(req)))
    checks = []

    def chk(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    res_ratio = out["residual_reduction_ratio"]
    chk("残余角速度 RMS 降低 >= 70%", res_ratio >= 0.70,
        "reduction=%.1f%% (%.3f -> %.3f deg/s)"
        % (100 * res_ratio, out["raw_rms"], out["residual_rms"]))
    chk("姿态误差 <= 0.5 deg（无陀螺仪丢包，抖动 2~9.5Hz / 运镜 0.03~0.1Hz）",
        SCEN_NOGAP_ERR <= 0.55,
        "max=%.3f deg, rms=%.3f deg" % (SCEN_NOGAP_ERR, SCEN_NOGAP_RMS))
    inner = _inner_band_pose_err(req, out, 2.0)
    chk("有丢包时姿态误差 <= 1.0 deg", out["truth_err_deg"] <= 1.0,
        "max=%.3f deg（含两段陀螺仪丢包），剔除首末 2s 后 %.3f deg"
        % (out["truth_err_deg"], inner))

    # 抖动抑制能力强弱：与"未做任何补偿"相比的残余抖动幅度
    chk("端点带内误差不超过内部口径的 3 倍", out["truth_err_deg"] <= 3.0 * max(inner, 0.1),
        "inner=%.3f, full=%.3f" % (inner, out["truth_err_deg"]))

    crops = out["crop_ratios"]
    chk("crop_ratio = 逐帧最大值", abs(out["crop_ratio"] - max(crops)) < 1e-12,
        "%.6f vs %.6f" % (out["crop_ratio"], max(crops)))
    chk("空缺率不超预算", out["crop_ratio"] <= out["crop_budget"] + 1e-12,
        "%.4f <= %.4f" % (out["crop_ratio"], out["crop_budget"]))
    chk("空缺率在 (0,1) 内", all(0.0 <= c <= 1.0 for c in crops),
        "min=%.4f max=%.4f" % (min(crops), max(crops)))

    # 逐帧空缺率与独立数值复核一致
    f, corners, hw, hh = _camera_geom(_cfg(req))
    worst_err = 0.0
    for i in range(0, len(out["comp"]), max(1, len(out["comp"]) // 12)):
        C = out["comp"][i]
        a = crop_ratio_for(f, corners, hw, hh, C)
        b = _grid_crop_estimate(f, corners, hw, hh, C, n=300)
        worst_err = max(worst_err, abs(a - b))
    chk("空缺率与逐帧数值复核一致 (<=2e-3)", worst_err <= 2e-3,
        "max |diff| = %.2e (n=300 网格；解析裁剪 vs 逐点反投影)" % worst_err)

    # 补偿矩阵正交性
    orth = 0.0
    for C in out["comp"]:
        P = mat3_mul(C, mat3_transpose(C))
        for i in range(3):
            for j in range(3):
                orth = max(orth, abs(P[i][j] - (1.0 if i == j else 0.0)))
    chk("补偿矩阵正交 (R R^T = I)", orth <= 1e-9, "max dev = %.2e" % orth)

    # 丢包
    gaps = out["gyro_gaps"]
    chk("两段陀螺仪丢包均被识别", out["gyro_gap_count"] >= 2,
        "gaps=%d -> %s" % (len(gaps), [(round(g[0], 2), round(g[1], 2), g[3])
                                       for g in gaps]))
    chk("帧丢帧被记录", len(out["frame_drops"]) >= 2,
        "frame_drops=%d" % len(out["frame_drops"]))

    # 丢包区间内姿态无跳变（相邻帧角速度有界）
    raw = out["raw_poses"]
    frames = req["frames"]
    jump = 0.0
    for i in range(1, len(raw)):
        dt = max(frames[i] - frames[i - 1], 1e-6)
        for a in range(3):
            jump = max(jump, abs(raw[i][a] - raw[i - 1][a]) / dt)
    chk("丢包区间姿态连续 (角速度 < 90 deg/s)", jump < 90.0,
        "max |dθ/dt| = %.1f deg/s" % jump)

    # 确定性 1：不同平滑窗/邻域实现（数值必须逐位一致；impl 仅记录元数据）
    r2 = dict(req)
    r2["smoother_impl"] = "scan"
    out2 = json.loads(json.dumps(run(r2)))
    numeric_keys = [k for k in out if k != "smoother"]
    same_impl = all(json.dumps(out[k], sort_keys=True)
                    == json.dumps(out2[k], sort_keys=True)
                    for k in numeric_keys)
    chk("bisect / scan 两种窗实现结果一致", same_impl)

    # 确定性 2：陀螺仪输入顺序打乱
    r3 = dict(req)
    shuffled = list(req["gyro"])
    rng = _Rng(99)
    for i in range(len(shuffled) - 1, 0, -1):
        j = int(rng.u() * (i + 1))
        shuffled[i], shuffled[j] = shuffled[j], shuffled[i]
    r3["gyro"] = shuffled
    out3 = json.loads(json.dumps(run(r3)))
    chk("输入乱序后结果一致",
        json.dumps(out, sort_keys=True) == json.dumps(out3, sort_keys=True))

    # 确定性 3：重复调用
    out4 = json.loads(json.dumps(run(req)))
    chk("重复运行结果逐位一致",
        json.dumps(out, sort_keys=True) == json.dumps(out4, sort_keys=True))

    # 稳定强度 ↔ 空缺率单调
    strong = dict(req)
    strong["crop_budget"] = 1.0
    out5 = run(strong)
    chk("补偿增益取满时不超预算", out5["crop_ratio"] <= 1.0,
        "crop@full=%.4f, gain=%.3f" % (out5["crop_ratio"], out5["comp_gain"]))

    ok = all(c[1] for c in checks)
    print("== stab.py 自检 ==")
    for name, good, detail in checks:
        print("[%s] %s%s" % ("PASS" if good else "FAIL", name,
                             ("  (" + detail + ")") if detail else ""))
    print("== %s ==" % ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

