#!/usr/bin/env python3
"""PCSS —— 百分比近似软阴影（percentage-closer soft shadows，可变半影）。

用法::

    python3 pcss.py req.json output.json [options]

请求 JSON（除 ``shadow`` / ``query`` 外全部可省略）::

    {
      "shadow": "d.json",                  # 阴影贴图：路径 / 行列表 / {width,height,data}
      "query": [{"x": 10, "y": 12, "z": 0.7, "id": "a"}],
      "light_size": 0.1,                   # 光源尺寸（阴影贴图纹素单位）
      "blocker_r": 8,                      # 遮挡物搜索半窗宽（纹素）
      "min_kernel": 1, "max_kernel": 16,   # 滤波半径夹取范围（纹素）
      "depth_bias": 1e-6,                  # 遮挡阈值：d < z - depth_bias
      "near_depth": 0.0,                   # d <= near_depth 视为穿透近裁剪面
      "far_depth": 1.0                     # d >= far_depth 视为空（无遮挡物）
    }

输出::

    {
      "lit": [{"x":.., "y":.., "z":.., "v":.., "kernel":..,
               "blockers":.., "avg_depth":.., "coverage":.., "taps":.., ...}],
      "avg_depth": {query_key: avg_depth_or_null},
      "digest": "sha256...",
      "stats": {...}, "params": {...}
    }

三个步骤（每个查询点独立、可并行）:

1. **遮挡物搜索**——在以查询点为中心、``blocker_r`` 为半径的阴影贴图窗口里按
   深度阈值 ``d < z - depth_bias`` 找出遮挡物。窗口会被裁剪到贴图范围内，
   实际覆盖比例记入 ``coverage``（部分覆盖的搜索窗按实际纹素统计）；查询点
   完全在贴图外时直接判为受光并标记 ``status="offmap"``。深度 ``d <= near_depth``
   的纹素视为**穿透近裁剪面**，被钳到近平面并按最近的遮挡物参与平均，而不是
   被当作空白丢掉（否则近裁剪面切掉的遮挡物会造成漏光）；``d >= far_depth``、
   ``NaN``、``None`` 视为空。

2. **半影半径**——``R = light_size * (z - avg_blocker) / avg_blocker``，夹取到
   ``[min_kernel, max_kernel]``。``z -> avg_blocker`` 时半径收缩到最小核，即
   接触硬化；``avg_blocker -> 0`` 时取最大核。**零遮挡时直接全亮**，不产生
   任何滤波采样（``taps = 0``），不做全图采样。

3. **可变核滤波**——在半径 ``R`` 的圆盘内取确定性的黄金角螺旋样本，可见样本
   占即光照值。圆盘滤波把硬阶跃抹成宽度 ``2R`` 的过渡带，恰好对应半径
   ``light_size`` 的面光源在遮挡深度 ``avg`` 处的解析半影宽度
   ``2 * light_size * (z - avg) / avg``，因此近处（z≈avg）硬化、远处变宽。

若 ``R`` 超过遮挡搜索窗（``adaptive_search``，默认开启），搜索窗会扩展到
``min(R, max_kernel)`` 重搜一次，避免半影被搜索窗截断（否则远场半影停在
``blocker_r`` 处，出现条带）。

确定性：遮挡计数与深度求和都在整数（定点，默认 2^24）上累加，滤波可见性用
整数样本计数，摘要按规范顺序 ``(y, x, z, id)`` 序列化。因此遮挡搜索的块遍历
顺序（row/col/snake/reverse/tile*/random）、分块大小与线程数都不改变阴影掩码、
半径统计和 ``digest``。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor

__all__ = [
    "DEFAULTS",
    "ORDER_CHOICES",
    "ShadowMap",
    "compute",
    "run",
    "load_request",
    "load_shadow",
    "disk_points",
    "tap_count",
    "main",
]

#: 所有可调参数及其默认值（请求里的同名字段覆盖默认值）。
DEFAULTS = {
    "light_size": 1.0,        # 光源半径（阴影贴图纹素单位）
    "blocker_r": 8.0,         # 遮挡搜索半窗宽（纹素）
    "min_kernel": 1.0,        # 最小滤波半径（接触硬化下限）
    "max_kernel": 16.0,       # 最大滤波半径（远场半影上限）
    "depth_bias": 1e-6,       # 遮挡阈值偏移：遮挡条件 d < z - depth_bias
    "near_depth": 0.0,        # d <= near_depth：穿透近裁剪面，钳到近平面
    "far_depth": 1.0,         # d >= far_depth：空（无遮挡物）
    "max_taps": 512,          # 滤波采样数上限
    "min_taps": 32,           # 滤波采样数下限
    "tap_density": 2.0,       # 每平方纹素采样数（乘 pi*R^2 后夹取）
    "kernel_shape": "disk",   # 核形状：disk（圆盘面光源）或 box（方形面光源）
    "adaptive_search": True,  # R > blocker_r 时扩展搜索窗重搜一次
    "order": "row",           # 遮挡窗口遍历顺序
    "tile": 8,                # 分块遍历的块边长
    "threads": 1,             # 查询级并行线程数（不影响结果）
    "seed": 0,                # random / tile_snake 遍历的随机种子
    "quant_scale": 1 << 24,   # 遮挡深度求和的定点精度
    "edge_mode": "skip",      # 滤波采样越界：skip（按有效样本归一化）或 clamp
    "filter_interp": "nearest",  # 滤波取样：nearest 或 bilinear
}

#: 遮挡搜索窗口的遍历顺序。
ORDER_CHOICES = (
    "row",
    "col",
    "snake",
    "reverse",
    "random",
    "tile",
    "tile_col",
    "tile_snake",
)

# 可见度判定阈值：v 在此范围内视为全亮 / 全暗。
_LIT_EPS = 1e-12


def _as_depth(value):
    """把贴图里的一个值转成深度；``None`` / 非数字记为空（``inf``）。

    空纹素不会遮挡任何东西，因此在金字塔里也按 ``inf`` 参与最小值，
    与 :func:`classify` 的 ``"empty"`` 分类一致。
    """
    if value is None:
        return float("inf")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")

# ---------------------------------------------------------------------------
# 阴影贴图
# ---------------------------------------------------------------------------


class ShadowMap:
    """不可变阴影贴图。行优先；纹素 ``(i, j)`` 的中心位于坐标 ``(i, j)``。"""

    __slots__ = ("rows", "width", "height", "_pyramid")

    #: 深度金字塔的最大层数（2^6 = 64 纹素块的 O(1) 邻域查询）
    _PYRAMID_LEVELS = 7

    def __init__(self, rows):
        rows = list(rows)
        if not rows:
            raise ValueError("shadow map is empty")
        parsed = []
        for row in rows:
            if not isinstance(row, (list, tuple)):
                raise ValueError("shadow map rows must be lists")
            if not row:
                raise ValueError("shadow map has an empty row")
            parsed.append([_as_depth(v) for v in row])
        width = min(len(r) for r in parsed)
        height = len(parsed)
        # 参差行按最短行截断，保证矩形访问安全。
        if any(len(r) != width for r in parsed):
            parsed = [r[:width] for r in parsed]
        self.rows = parsed
        self.width = width
        self.height = height
        self._pyramid = None

    def __repr__(self):
        return "ShadowMap(%dx%d)" % (self.width, self.height)

    # -- 最小深度金字塔 ---------------------------------------------------
    #
    # 第 0 层是原始深度；第 L 层每个格子是 2^L x 2^L 纹素块的最小深度。
    # 用它可以在 O(层数) 内判断"最大核范围内是否真的存在遮挡物"，从而跳过
    # 大片空白区域的开窗扫描（无遮挡时不做任何滤波采样）。
    # 逐层按 2 倍下采样、奇数边界直接丢弃尾部，因此第 L 层格子严格等于
    # ``[i*2^L, (i+1)*2^L) x [j*2^L, (j+1)*2^L)`` 的最小值；边缘不足的部分
    # 单独按 ceil 补齐，保证不漏纹素。

    def _build_pyramid(self):
        pyramid = [self.rows]
        while len(pyramid) < self._PYRAMID_LEVELS:
            base = pyramid[-1]
            bh = len(base)
            bw = len(base[0])
            if bw == 1 and bh == 1:
                break
            nh = (bh + 1) // 2
            nw = (bw + 1) // 2
            lvl = []
            for j in range(nh):
                r0 = base[2 * j]
                r1 = base[2 * j + 1] if 2 * j + 1 < bh else r0
                row = []
                for i in range(nw):
                    i0 = 2 * i
                    m = r0[i0]
                    if r1 is not r0:
                        v = r1[i0]
                        if v < m:
                            m = v
                    if i0 + 1 < bw:
                        v = r0[i0 + 1]
                        if v < m:
                            m = v
                        if r1 is not r0:
                            v = r1[i0 + 1]
                            if v < m:
                                m = v
                    row.append(m)
                lvl.append(row)
            pyramid.append(lvl)
        return pyramid

    def min_block_depth(self, i0, i1, j0, j1, cutoff=None):
        """闭区间 ``[i0,i1] x [j0,j1]`` 内纹素的最小深度（精确值）。

        从最高层递归下降：完全落在区间内的块直接整块取最小值，跨边界的块拆成
        4 个子块。第 ``L`` 层格子数按 ``ceil`` 补齐，因此下标始终合法。
        区间完全在贴图外时返回 ``inf``。
        """
        x0 = max(i0, 0)
        x1 = min(i1, self.width - 1)
        y0 = max(j0, 0)
        y1 = min(j1, self.height - 1)
        if x1 < x0 or y1 < y0:
            return float("inf")
        pyramid = self._pyramid
        if pyramid is None:
            pyramid = self._pyramid = self._build_pyramid()
        levels = len(pyramid)
        best = float("inf")

        def descend(level, bi, bj):
            """块 (bi,bj)@level 与询问区间交集的最小深度；无交集返回 inf。"""
            nonlocal best
            size = 1 << level
            bx0 = bi * size
            by0 = bj * size
            if bx0 > x1 or by0 > y1 or bx0 + size - 1 < x0 or by0 + size - 1 < y0:
                return
            if x0 <= bx0 and bx0 + size - 1 <= x1 and y0 <= by0 and by0 + size - 1 <= y1:
                v = pyramid[level][bj][bi]
                if v < best:
                    best = v
                return
            if level == 0:
                v = self.rows[by0][bx0]
                if v < best:
                    best = v
                return
            # 跨边界：只保留与询问区间相交的子块
            half = size >> 1
            bis = tuple(b for b in (bi * 2, bi * 2 + 1)
                        if b * half <= x1 and (b + 1) * half - 1 >= x0)
            bjs = tuple(b for b in (bj * 2, bj * 2 + 1)
                        if b * half <= y1 and (b + 1) * half - 1 >= y0)
            for cbj in bjs:
                for cbi in bis:
                    descend(level - 1, cbi, cbj)

        top = levels - 1
        side = 1 << top
        for bj in range(y0 // side, y1 // side + 1):
            for bi in range(x0 // side, x1 // side + 1):
                descend(top, bi, bj)
                if cutoff is not None and best < cutoff:
                    return best
        return best

    def any_blocker(self, i0, i1, j0, j1, cutoff):
        """区间内是否存在深度 < ``cutoff`` 的纹素。

        与逐纹素扫描等价（金字塔存的是精确最小值，不是近似）。
        """
        return self.min_block_depth(i0, i1, j0, j1, cutoff) < cutoff


def _rows_from_flat(data, width, height, where):
    w = int(width or 0)
    h = int(height or 0)
    if w <= 0 and h > 0:
        w = int(math.ceil(len(data) / float(h)))
    if h <= 0 and w > 0:
        h = int(math.ceil(len(data) / float(w)))
    if w <= 0 or h <= 0:
        raise ValueError("%s: flat shadow data needs 'width' and 'height'" % where)
    if len(data) < w * h:
        raise ValueError(
            "%s: flat shadow data has %d values, need %d" % (where, len(data), w * h)
        )
    return [list(data[j * w:(j + 1) * w]) for j in range(h)]


def load_shadow(spec, base_dir=None, width=None, height=None):
    """把请求里的 ``shadow`` 字段解析为 :class:`ShadowMap`。

    支持四种写法：

    * 路径字符串——相对路径依次相对 ``base_dir``、当前目录、请求文件目录解析；
      被引用文件本身可以是行列表或本函数接受的其它形式；
    * 嵌套行列表 ``[[d00, d01, ...], ...]``；
    * 扁平列表 + 请求里的 ``width`` / ``height``；
    * ``{"width": w, "height": h, "data": [...]}`` 或 ``{"rows": [[...]]}``。

    另外允许 ``"shadow": "d.json"`` 指向一个 ``{"width":..,"height":..,"data":..}``
    的对象文件。
    """
    if spec is None:
        raise ValueError("request is missing 'shadow'")

    if isinstance(spec, str):
        path = spec
        if not os.path.isabs(path):
            candidates = []
            if base_dir:
                candidates.append(os.path.join(base_dir, path))
            candidates.append(os.path.join(os.getcwd(), path))
            for cand in candidates:
                if os.path.exists(cand):
                    path = cand
                    break
        with open(path, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        return load_shadow(
            loaded, os.path.dirname(os.path.abspath(path)), width, height
        )

    if isinstance(spec, dict):
        if "rows" in spec:
            return ShadowMap(spec["rows"])
        if "data" in spec:
            rows = _rows_from_flat(
                spec["data"],
                spec.get("width", width),
                spec.get("height", height),
                "shadow.data",
            )
            return ShadowMap(rows)
        if "shadow" in spec:
            return load_shadow(spec["shadow"], base_dir, width, height)
        raise ValueError(
            "unsupported shadow object with keys %s" % (sorted(spec),)
        )

    if isinstance(spec, (list, tuple)):
        if not spec:
            raise ValueError("shadow map is empty")
        if isinstance(spec[0], (list, tuple)):
            return ShadowMap(spec)
        return ShadowMap(_rows_from_flat(spec, width, height, "shadow"))

    raise TypeError("unsupported shadow map type %r" % (type(spec),))


# ---------------------------------------------------------------------------
# 遮挡搜索窗的遍历顺序
# ---------------------------------------------------------------------------


def window_cells(i0, i1, j0, j1, order, tile, seed):
    """按给定顺序枚举闭区间窗口 ``[i0,i1] x [j0,j1]`` 内的所有纹素。

    顺序只决定执行次序；调用方用整数累加，因此任何顺序结果都一样。
    """
    if i1 < i0 or j1 < j0:
        return []
    if order == "col":
        return [(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)]
    if order == "snake":
        out = []
        flip = False
        for j in range(j0, j1 + 1):
            xs = range(i1, i0 - 1, -1) if flip else range(i0, i1 + 1)
            out.extend((i, j) for i in xs)
            flip = not flip
        return out
    if order == "reverse":
        return [(i, j) for j in range(j1, j0 - 1, -1) for i in range(i1, i0 - 1, -1)]
    if order in ("tile", "tile_col", "tile_snake"):
        t = max(1, int(tile))
        bis = list(range(i0, i1 + 1, t))
        bjs = list(range(j0, j1 + 1, t))
        blocks = []
        if order == "tile_col":
            for bi in bis:
                for bj in bjs:
                    blocks.append((bi, bj))
        else:
            for bj in bjs:
                for bi in bis:
                    blocks.append((bi, bj))
        out = []
        alternate = False
        for bi, bj in blocks:
            xs = range(bi, min(bi + t, i1 + 1))
            chunk = [(i, j) for j in range(bj, min(bj + t, j1 + 1)) for i in xs]
            if order == "tile_snake" and alternate:
                chunk.reverse()
            out.extend(chunk)
            alternate = not alternate
        return out
    if order == "random":
        coords = [(i, j) for j in range(j0, j1 + 1) for i in range(i0, i1 + 1)]
        # 每个查询独立、按窗口几何决定的 RNG：只影响次序，不影响统计。
        rng = random.Random(
            (int(seed) * 0x9E3779B1 + i0 * 73856093 + j0 * 19349663
             + i1 * 83492791 + j1 * 2654435761) & 0xFFFFFFFF
        )
        rng.shuffle(coords)
        return coords
    # row 以及未知顺序的安全回退
    return [(i, j) for j in range(j0, j1 + 1) for i in range(i0, i1 + 1)]


# ---------------------------------------------------------------------------
# 圆盘采样（黄金角螺旋：确定性、面积均匀、无随机跳变）
# ---------------------------------------------------------------------------

_DISK_CACHE = {}
_GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))


def disk_points(n):
    """返回 ``n`` 个单位圆盘内的采样点 ``[(x, y), ...]``，半径 <= 1。"""
    if n <= 0:
        return ()
    pts = _DISK_CACHE.get(n)
    if pts is None:
        pts = tuple(
            (
                math.sqrt((k + 0.5) / n) * math.cos(k * _GOLDEN_ANGLE),
                math.sqrt((k + 0.5) / n) * math.sin(k * _GOLDEN_ANGLE),
            )
            for k in range(n)
        )
        _DISK_CACHE[n] = pts
    return pts


def kernel_points(n, shape="disk"):
    """返回 ``n`` 个核内采样点 ``[(x, y), ...]``，坐标在 ``[-1, 1]`` 内。

    * ``disk``：黄金角螺旋，覆盖单位圆盘（圆盘面光源，半影宽 = 2R）；
    * ``box``：规则网格，覆盖 ``[-1,1]^2``（方形面光源，半影宽也是 2R，
      但过渡带剖面是线性的而不是圆盘面积曲线）。
    """
    if n <= 0:
        return ()
    if shape == "box":
        side = max(1, int(math.ceil(math.sqrt(n))))
        pts = []
        for j in range(side):
            py = -1.0 + 2.0 * (j + 0.5) / side
            for i in range(side):
                px = -1.0 + 2.0 * (i + 0.5) / side
                pts.append((px, py))
        return tuple(pts)
    return disk_points(n)


def tap_count(radius, cfg):
    """按半径面积决定采样数，夹取到 ``[min_taps, max_taps]``。"""
    if radius <= 0.0:
        return 1
    n = int(round(float(cfg["tap_density"]) * math.pi * radius * radius))
    return max(int(cfg["min_taps"]), min(int(cfg["max_taps"]), n))


# ---------------------------------------------------------------------------
# 深度分类
# ---------------------------------------------------------------------------


def classify(depth, near, far):
    """把贴图深度分类为 ``("empty", None)`` / ``("penetrating", near)`` / ``("solid", d)``。

    * ``None`` / ``NaN`` / ``±inf`` / ``d >= far`` → 空，不遮挡。
    * ``d <= near`` → 穿透近裁剪面；钳到近平面并按最近遮挡物计入平均，
      防止近裁剪面切掉遮挡物时出现漏光。
    * 其它 → 正常实体深度。
    """
    if depth is None:
        return "empty", None
    try:
        d = float(depth)
    except (TypeError, ValueError):
        return "empty", None
    if not math.isfinite(d):
        return "empty", None
    if d <= near:
        return "penetrating", float(near)
    if d >= far:
        return "empty", None
    return "solid", d


# ---------------------------------------------------------------------------
# 遮挡搜索窗统计
# ---------------------------------------------------------------------------


def _window_bounds(sm, x, y, r):
    """返回裁剪到贴图内的整型窗口 ``(i0, i1, j0, j1, nominal_cells)``。"""
    i0 = int(math.ceil(x - r))
    i1 = int(math.floor(x + r))
    j0 = int(math.ceil(y - r))
    j1 = int(math.floor(y + r))
    nominal = max(1.0, (2.0 * r + 1.0) ** 2)
    i0c = max(0, i0)
    i1c = min(sm.width - 1, i1)
    j0c = max(0, j0)
    j1c = min(sm.height - 1, j1)
    return i0c, i1c, j0c, j1c, nominal


def _search_blockers(sm, x, y, r, threshold, cfg):
    """在半径 ``r`` 的窗口内统计遮挡物。

    返回 ``(count, depth_ticks, penetrating, coverage)``；窗口完全落在
    贴图外时返回 ``None``（调用方按受光处理）。
    """
    i0, i1, j0, j1, nominal = _window_bounds(sm, x, y, r)
    if i1 < i0 or j1 < j0:
        return None
    near = cfg["near_depth"]
    far = cfg["far_depth"]
    quant = cfg["quant_scale"]
    count = 0
    ticks = 0
    penetrating = 0
    cells = window_cells(i0, i1, j0, j1, cfg["order"], cfg["tile"], cfg["seed"])
    rows = sm.rows
    for i, j in cells:
        kind, d = classify(rows[j][i], near, far)
        if kind == "empty":
            continue
        if kind == "penetrating":
            penetrating += 1
        if d < threshold:
            count += 1
            ticks += int(round(d * quant))
    covered = max(1, (i1 - i0 + 1) * (j1 - j0 + 1))
    coverage = min(1.0, covered / nominal)
    return count, ticks, penetrating, coverage


# ---------------------------------------------------------------------------
# 滤波取样
# ---------------------------------------------------------------------------


def _sample_tap(sm, px, py, threshold, cfg):
    """在 ``(px, py)`` 取样一次，返回 ``(visible_weight, weight)``。

    越界处理：
    * ``edge_mode="skip"``：越界角落不参与（等价于把贴图外当作受光）；
    * ``edge_mode="clamp"``：越界角落钳到边界纹素。

    权重为 0 表示该样本整体落在贴图外、应被跳过。
    """
    near = cfg["near_depth"]
    far = cfg["far_depth"]
    width = sm.width
    height = sm.height
    rows = sm.rows
    visible_w = 0.0
    weight = 0.0

    def texel(i, j, w):
        nonlocal visible_w, weight
        kind, d = classify(rows[j][i], near, far)
        weight += w
        if kind == "empty" or d >= threshold:
            visible_w += w

    if cfg["filter_interp"] == "bilinear":
        x0 = int(math.floor(px))
        y0 = int(math.floor(py))
        tx = px - x0
        ty = py - y0
        corners = (
            (x0, y0, (1.0 - tx) * (1.0 - ty)),
            (x0 + 1, y0, tx * (1.0 - ty)),
            (x0, y0 + 1, (1.0 - tx) * ty),
            (x0 + 1, y0 + 1, tx * ty),
        )
        for i, j, w in corners:
            if w <= 0.0:
                continue
            if i < 0 or i >= width or j < 0 or j >= height:
                if cfg["edge_mode"] != "clamp":
                    continue
                i = min(max(i, 0), width - 1)
                j = min(max(j, 0), height - 1)
            texel(i, j, w)
        return visible_w, weight

    i = int(math.floor(px + 0.5))
    j = int(math.floor(py + 0.5))
    if i < 0 or i >= width or j < 0 or j >= height:
        if cfg["edge_mode"] != "clamp":
            return 0.0, 0.0
        i = min(max(i, 0), width - 1)
        j = min(max(j, 0), height - 1)
    texel(i, j, 1.0)
    return visible_w, weight


def _filter_visibility(sm, x, y, radius, threshold, cfg):
    """在半径 ``radius`` 的核上滤波，返回 ``(visibility, taps_used)``。"""
    n = tap_count(radius, cfg)
    shape = cfg["kernel_shape"]
    visible_w = 0.0
    weight = 0.0
    used = 0
    for ux, uy in kernel_points(n, shape):
        wv, w = _sample_tap(sm, x + ux * radius, y + uy * radius, threshold, cfg)
        if w <= 0.0:
            continue
        used += 1
        visible_w += wv
        weight += w
    if weight <= 0.0:
        return 1.0, 0
    return visible_w / weight, used


# ---------------------------------------------------------------------------
# 单点着色
# ---------------------------------------------------------------------------


def _penumbra_radius(z, avg_depth, cfg):
    """由平均遮挡深度与受光距离推导滤波半径，夹取到 [min_kernel, max_kernel]。"""
    light_size = cfg["light_size"]
    min_k = cfg["min_kernel"]
    max_k = cfg["max_kernel"]
    if avg_depth <= 0.0:
        return max_k
    r = light_size * (z - avg_depth) / avg_depth
    if not math.isfinite(r) or r <= 0.0:
        return max_k
    return max(min_k, min(max_k, r))


def _shade_query(query, sm, cfg):
    """计算单个查询点，返回一条 :class:`dict` 记录。"""
    entry = {
        "x": None,
        "y": None,
        "z": None,
        "v": 1.0,
        "kernel": 0.0,
        "blockers": 0,
        "taps": 0,
        "avg_depth": None,
        "coverage": 0.0,
        "penetrating": 0,
        "search_r": 0.0,
        "penumbra_width": 0.0,
        "status": "lit",
    }
    qid = query.get("id")
    if qid is not None:
        entry["id"] = qid
    for key in ("x", "y", "z"):
        value = query.get(key)
        entry[key] = value
        try:
            entry["_" + key] = float(value)
        except (TypeError, ValueError):
            entry["status"] = "invalid"
            return entry
        if not math.isfinite(entry["_" + key]):
            entry["status"] = "invalid"
            return entry

    x = entry["_x"]
    y = entry["_y"]
    z = entry["_z"]
    near = cfg["near_depth"]
    far = cfg["far_depth"]
    if z <= near:
        # 接收点位于近裁剪面以内：遮挡信息不可用，保守判为受光。
        entry["status"] = "lit"
        return entry

    # 遮挡阈值在 z 处；不把 z 钳到 far —— 接收点可以在远平面之外，
    # 钳制会伪造"贴近遮挡物"的假象并把半影压到最小核。
    threshold = z - cfg["depth_bias"]
    blocker_r = cfg["blocker_r"]

    stats = _search_blockers(sm, x, y, blocker_r, threshold, cfg)
    if stats is None:
        entry["status"] = "offmap"
        return entry

    count, ticks, penetrating, coverage = stats
    entry["coverage"] = coverage
    entry["penetrating"] = penetrating
    entry["search_r"] = blocker_r

    if count == 0:
        # 窄窗里没找到遮挡物。但半影可以比窄窗宽：遮挡物只要在最大核范围内，
        # 仍然能通过滤波把阴影投到这里。直接用最小深度金字塔做一次
        # "最大核范围内有没有遮挡物"的判定；没有就真的全亮（不采样）。
        reach = int(math.ceil(cfg["max_kernel"]))
        if (cfg["adaptive_search"]
                and sm.any_blocker(int(math.floor(x)) - reach,
                                   int(math.ceil(x)) + reach,
                                   int(math.floor(y)) - reach,
                                   int(math.ceil(y)) + reach,
                                   threshold)):
            stats = _search_blockers(sm, x, y, float(reach), threshold, cfg)
            if stats is not None:
                count, ticks, penetrating, coverage = stats
                entry["coverage"] = coverage
                entry["penetrating"] = penetrating
                entry["search_r"] = float(reach)
        if count == 0:
            # 确实零遮挡：直接全亮，不做任何滤波采样。
            entry["blockers"] = 0
            entry["status"] = "lit"
            return entry

    quant = cfg["quant_scale"]
    avg_depth = ticks / (float(quant) * count)
    radius = _penumbra_radius(z, avg_depth, cfg)

    # 半影仍超出当前搜索窗时再扩展一次（避免远场半影被窗口截断、出现条带）。
    if cfg["adaptive_search"] and radius > entry["search_r"]:
        search_r = min(max(radius, entry["search_r"]), cfg["max_kernel"])
        expanded = _search_blockers(sm, x, y, search_r, threshold, cfg)
        if expanded is not None and expanded[0] > 0:
            count, ticks, penetrating, coverage = expanded
            entry["search_r"] = search_r
            entry["coverage"] = coverage
            entry["penetrating"] = penetrating
            avg_depth = ticks / (float(quant) * count)
            radius = _penumbra_radius(z, avg_depth, cfg)

    entry["blockers"] = count
    entry["avg_depth"] = avg_depth
    entry["kernel"] = radius
    # 核半径 R 的过渡带宽度 = 2R；这就是解析半影宽 2*light_size*(z-avg)/avg
    # （被 min/max_kernel 夹取后的版本）。
    entry["penumbra_width"] = 2.0 * radius

    # ---- 可变半径圆盘滤波 ------------------------------------------------
    visibility, taps = _filter_visibility(sm, x, y, radius, threshold, cfg)
    visibility = min(1.0, max(0.0, visibility))
    entry["taps"] = taps
    entry["v"] = visibility
    if visibility >= 1.0 - _LIT_EPS:
        entry["status"] = "lit"
    elif visibility <= _LIT_EPS:
        entry["status"] = "umbra"
    else:
        entry["status"] = "penumbra"
    return entry


_PUBLIC_FIELDS = (
    "id", "x", "y", "z", "v", "kernel", "penumbra_width", "blockers",
    "avg_depth", "coverage", "taps", "penetrating", "search_r", "status",
)


def _clean_entry(entry):
    """只输出公开字段，丢掉 ``_x``/``_y``/``_z`` 等内部临时键。"""
    return {k: entry[k] for k in _PUBLIC_FIELDS if k in entry}


# ---------------------------------------------------------------------------
# 配置归一化
# ---------------------------------------------------------------------------


def _make_config(request, overrides):
    cfg = dict(DEFAULTS)
    for key in DEFAULTS:
        if request.get(key) is not None:
            cfg[key] = request[key]
    for key, value in (overrides or {}).items():
        if value is not None and key in DEFAULTS:
            cfg[key] = value

    cfg["light_size"] = max(0.0, float(cfg["light_size"]))
    cfg["blocker_r"] = max(0.0, float(cfg["blocker_r"]))
    cfg["min_kernel"] = max(0.0, float(cfg["min_kernel"]))
    cfg["max_kernel"] = max(cfg["min_kernel"], float(cfg["max_kernel"]))
    cfg["depth_bias"] = float(cfg["depth_bias"])
    cfg["near_depth"] = float(cfg["near_depth"])
    cfg["far_depth"] = float(cfg["far_depth"])
    if cfg["far_depth"] <= cfg["near_depth"]:
        cfg["far_depth"] = cfg["near_depth"] + 1.0
    cfg["min_taps"] = max(1, int(cfg["min_taps"]))
    cfg["max_taps"] = max(cfg["min_taps"], int(cfg["max_taps"]))
    cfg["tap_density"] = max(0.0, float(cfg["tap_density"]))
    cfg["adaptive_search"] = bool(cfg["adaptive_search"])
    cfg["tile"] = max(1, int(cfg["tile"]))
    cfg["threads"] = max(1, int(cfg["threads"]))
    cfg["seed"] = int(cfg["seed"])
    cfg["quant_scale"] = max(1, int(cfg["quant_scale"]))
    order = str(cfg["order"])
    cfg["order"] = order if order in ORDER_CHOICES else "row"
    edge = str(cfg["edge_mode"])
    cfg["edge_mode"] = edge if edge in ("skip", "clamp") else "skip"
    interp = str(cfg["filter_interp"])
    cfg["filter_interp"] = interp if interp in ("nearest", "bilinear") else "nearest"
    shape = str(cfg["kernel_shape"])
    cfg["kernel_shape"] = shape if shape in ("disk", "box") else "disk"
    return cfg


# ---------------------------------------------------------------------------
# 统计与摘要
# ---------------------------------------------------------------------------


def _summarise(entries):
    """汇总分像素遮挡统计与核半径分布。"""
    lit = penumbra = umbra = 0
    no_blocker = offmap = invalid = 0
    blockers_total = taps_total = penetrating_total = 0
    coverage_min = 1.0
    hist = {}
    for e in entries:
        v = e["v"]
        if v >= 1.0 - _LIT_EPS:
            lit += 1
        elif v <= _LIT_EPS:
            umbra += 1
        else:
            penumbra += 1
        status = e["status"]
        if status == "offmap":
            offmap += 1
        elif status == "invalid":
            invalid += 1
        if e["blockers"] == 0:
            no_blocker += 1
        blockers_total += e["blockers"]
        taps_total += e["taps"]
        penetrating_total += e["penetrating"]
        coverage_min = min(coverage_min, e["coverage"])
        key = round(float(e["kernel"]), 6)
        hist[key] = hist.get(key, 0) + 1

    active = [k for k in hist if k > 0.0]
    if active:
        k_min = min(active)
        k_max = max(active)
        k_mean = sum(k * hist[k] for k in active) / float(sum(hist[k] for k in active))
    else:
        k_min = k_max = k_mean = 0.0

    return {
        "queries": len(entries),
        "lit": lit,
        "penumbra": penumbra,
        "umbra": umbra,
        "no_blocker": no_blocker,
        "offmap": offmap,
        "invalid": invalid,
        "blockers_total": blockers_total,
        "penetrating_hits": penetrating_total,
        "taps_total": taps_total,
        "kernel_min": k_min,
        "kernel_max": k_max,
        "kernel_mean": k_mean,
        "kernel_distribution": [[k, hist[k]] for k in sorted(hist)],
        "coverage_min": coverage_min if entries else 0.0,
        "full_light": bool(entries) and lit == len(entries),
    }


def _canonical_item(e):
    id_key = "" if e.get("id") is None else str(e.get("id"))

    def num(key):
        try:
            v = float(e.get(key))
        except (TypeError, ValueError):
            return -1.0e300
        return v if math.isfinite(v) else -1.0e300

    return (num("y"), num("x"), num("z"), id_key)


def mask_digest(entries):
    """阴影掩码摘要（SHA-256），与计算顺序、块次序、线程数无关。"""
    return mask_digests(entries)[0]


def mask_digests(entries):
    """返回 ``(mask_digest, kernel_digest, blockers_digest)``。

    三者都以规范顺序 ``(y, x, z, id)`` 序列化：

    * ``mask_digest``：``[id, x, y, z, v, kernel]``，即阴影掩码 + 半径；
    * ``kernel_digest``：``[id, x, y, z, kernel]``，只看半径分布；
    * ``blockers_digest``：``[id, x, y, z, blockers, taps, coverage]``，只看遮挡统计。
    """
    mask_rows = []
    rad_rows = []
    occ_rows = []
    for e in sorted(entries, key=_canonical_item):
        id_key = None if e.get("id") is None else str(e.get("id"))

        def num(key):
            try:
                v = float(e.get(key))
            except (TypeError, ValueError):
                return -1.0e300
            return v if math.isfinite(v) else -1.0e300

        x, y, z = num("x"), num("y"), num("z")
        mask_rows.append([id_key, x, y, z, float(e["v"]), float(e["kernel"])])
        rad_rows.append([id_key, x, y, z, round(float(e["kernel"]), 9)])
        occ_rows.append(
            [
                id_key,
                x,
                y,
                z,
                int(e["blockers"]),
                int(e["taps"]),
                round(float(e["coverage"]), 9),
            ]
        )

    def digest(rows):
        blob = json.dumps(rows, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    return digest(mask_rows), digest(rad_rows), digest(occ_rows)


# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------


def compute(request, base_dir=None, **overrides):
    """计算 PCSS 结果并返回输出字典。

    参数
    ----
    request : dict
        请求字典（见模块文档）。
    base_dir : str, optional
        解析 ``shadow`` 相对路径的基准目录。
    **overrides
        覆盖 :data:`DEFAULTS` 中的任意参数，例如 ``order="col"``、``threads=4``。
    """
    if not isinstance(request, dict):
        raise TypeError("request must be a dict")
    cfg = _make_config(request, overrides)
    sm = load_shadow(
        request.get("shadow"),
        base_dir,
        request.get("width"),
        request.get("height"),
    )
    queries = request.get("query")
    if queries is None:
        queries = []
    if not isinstance(queries, list):
        raise ValueError("'query' must be a list of {x, y, z} objects")

    def work(q):
        if not isinstance(q, dict):
            raise ValueError("each query must be an object with x, y, z")
        return _shade_query(q, sm, cfg)

    threads = cfg["threads"]
    if threads > 1 and len(queries) > 1:
        with ThreadPoolExecutor(max_workers=threads) as pool:
            raw = list(pool.map(work, queries))
    else:
        raw = [work(q) for q in queries]

    avg_depth = {}
    for idx, e in enumerate(raw):
        key = e["id"] if "id" in e else str(idx)
        avg_depth[key] = e["avg_depth"]

    stats = _summarise(raw)
    digest, kernel_digest, blockers_digest = mask_digests(raw)
    entries = [_clean_entry(e) for e in raw]

    return {
        "lit": entries,
        "avg_depth": avg_depth,
        "digest": digest,
        "kernel_digest": kernel_digest,
        "blockers_digest": blockers_digest,
        "stats": stats,
        "params": {
            "light_size": cfg["light_size"],
            "blocker_r": cfg["blocker_r"],
            "min_kernel": cfg["min_kernel"],
            "max_kernel": cfg["max_kernel"],
            "depth_bias": cfg["depth_bias"],
            "near_depth": cfg["near_depth"],
            "far_depth": cfg["far_depth"],
            "order": cfg["order"],
            "tile": cfg["tile"],
            "threads": cfg["threads"],
            "seed": cfg["seed"],
            "min_taps": cfg["min_taps"],
            "max_taps": cfg["max_taps"],
            "tap_density": cfg["tap_density"],
            "kernel_shape": cfg["kernel_shape"],
            "adaptive_search": cfg["adaptive_search"],
            "edge_mode": cfg["edge_mode"],
            "filter_interp": cfg["filter_interp"],
        },
    }


def run(request, **overrides):
    """兼容旧接口：``run(req) -> dict``。"""
    return compute(request, **overrides)


def load_request(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------


def _parser():
    p = argparse.ArgumentParser(
        description="PCSS variable-penumbra soft shadows (deterministic, stdlib only)"
    )
    p.add_argument("request", help="request JSON path")
    p.add_argument("output", nargs="?", help="output JSON path (default: stdout)")
    p.add_argument("--order", choices=ORDER_CHOICES, help="blocker window traversal order")
    p.add_argument("--threads", type=int, help="query-level worker threads")
    p.add_argument("--tile", type=int, help="tile size for tiled traversal")
    p.add_argument("--seed", type=int, help="RNG seed for random/tile_snake traversal")
    p.add_argument("--light-size", type=float, help="light radius in shadow-map texels")
    p.add_argument("--blocker-r", type=float, help="blocker search half-window (texels)")
    p.add_argument("--min-kernel", type=float, help="minimum filter radius")
    p.add_argument("--max-kernel", type=float, help="maximum filter radius")
    p.add_argument("--depth-bias", type=float, help="depth threshold offset (z - bias)")
    p.add_argument("--max-taps", type=int, help="maximum filter taps")
    p.add_argument("--edge-mode", choices=("skip", "clamp"), help="out-of-bounds tap mode")
    p.add_argument(
        "--no-adaptive-search", action="store_true", help="disable search-window expansion"
    )
    p.add_argument("--pretty", action="store_true", help="indent output JSON")
    return p


def main(argv=None):
    args = _parser().parse_args(argv)
    base_dir = os.path.dirname(os.path.abspath(args.request))
    request = load_request(args.request)
    overrides = {
        "order": args.order,
        "threads": args.threads,
        "tile": args.tile,
        "seed": args.seed,
        "light_size": args.light_size,
        "blocker_r": args.blocker_r,
        "min_kernel": args.min_kernel,
        "max_kernel": args.max_kernel,
        "depth_bias": args.depth_bias,
        "max_taps": args.max_taps,
        "edge_mode": args.edge_mode,
        "adaptive_search": False if args.no_adaptive_search else None,
    }
    result = compute(request, base_dir=base_dir, **overrides)
    text = json.dumps(
        result,
        indent=2 if args.pretty else None,
        sort_keys=args.pretty,
        ensure_ascii=False,
    )
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.write("\n")
    else:
        sys.stdout.write(text)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

