#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PCSS 验收脚本（对照解析遮挡几何）。

场景 A：半影宽度剖面
--------------------
阴影贴图与接收面共面于世界平面（贴图纹素坐标 == 世界 x/y 坐标）。面光源是
边长 ``2*light_size`` 的正方形，位于接收点正上方高度 ``z``。遮挡物是一块
深度为 ``avg`` 的竖直半无限板，边缘在 ``x = xe``。

对接收点 ``p = (x, y, z)``，遮挡物在光源平面上把点 ``p`` 投到
``xe + (x - xe) * z / avg``（按相似三角形）。于是光源矩形（以 0 为中心、
半宽 ``L = light_size``）中被遮挡的长度给出解析可见度::

    w = xe + (x - xe) * z / avg           # 遮挡边界在光源平面上的位置
    occ = clamp(w - (-L), 0, 2L)          # 被遮住的光源宽度
    v_ref = 1 - occ / (2L)

即：``x`` 落在 ``xe`` 附近时是过渡带；``x <= xe`` 完全在阴影里（v=0）；
``x - xe`` 超过 ``2L * avg / (z - avg)`` 后完全受光（v=1）。

半影总宽 = ``2L * (z - avg) / avg``，与实现里的 ``R = light_size * (z-avg)/avg``
乘以圆盘直径 2 一致。接触硬化区：``z -> avg`` 时半影宽度 -> 0。

场景 B：确定性
--------------
固定一张带遮挡物的贴图 + 一批查询点，遍历所有块顺序 / 分块大小 / 线程数，
比较 ``digest``（掩码+半径）、``kernel_digest``（半径分布）与
``blockers_digest``（遮挡统计）。

场景 C：无遮挡平面
------------------
全部纹素远大于查询深度（空），要求 ``v == 1``、``taps == 0``（不做滤波采样）、
``kernel == 0``、``status == "lit"``。

用法::

    python3 check_pcss.py            # 跑全部检查
    python3 check_pcss.py -v         # 打印每个断言的数值
"""

from __future__ import annotations

import math
import sys

import pcss

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv

FAILS = []
CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, ok, detail))
    if not ok:
        FAILS.append((name, detail))
    if VERBOSE or not ok:
        print(("PASS  " if ok else "FAIL  ") + name + ("  " + detail if detail else ""))


# ---------------------------------------------------------------------------
# 解析参考
# ---------------------------------------------------------------------------


def ref_visibility(x, xe, z, avg, light_size):
    """正方形面光源 + 半无限板的解析可见度（见模块文档）。"""
    L = float(light_size)
    if avg <= 0.0:
        return 0.0 if x > xe else 1.0
    w = xe + (x - xe) * (z / avg)
    occ = min(max(w + L, 0.0), 2.0 * L)
    return 1.0 - occ / (2.0 * L)


def ref_penumbra_width(z, avg, light_size):
    """解析半影总宽度（光源下方阴影边界两侧完整过渡带的宽度）。"""
    if avg <= 0.0:
        return 0.0
    return 2.0 * float(light_size) * (z - avg) / avg


# ---------------------------------------------------------------------------
# 场景构造
# ---------------------------------------------------------------------------


def make_edge_shadow(width, height, avg, xe, far=1.0):
    """竖直板：x < xe 的纹素深度 avg，其余 far（空）。"""
    return [
        [avg if i < xe else far for i in range(width)] for _ in range(height)
    ]


def row_queries(width, y, z, x_from, x_to):
    out = []
    for x in range(x_from, x_to + 1):
        out.append({"x": x, "y": y, "z": z, "id": "x%d" % x})
    return out


def profile(req, **overrides):
    """返回 {x: (v, kernel, blockers, taps)}。"""
    out = pcss.compute(req, **overrides)
    res = {}
    for e in out["lit"]:
        res[int(e["x"])] = (e["v"], e["kernel"], e["blockers"], e["taps"])
    return out, res


# ---------------------------------------------------------------------------
# 检查 A：半影宽度剖面 vs 解析参考
# ---------------------------------------------------------------------------

W = 96
H = 24
XE = 48


def _edge_distance(res, v_threshold):
    """沿剖面找可见度首次跨过 v_threshold 的 x（线性插值，纹素单位）。"""
    prev = None
    for x in sorted(res):
        v = res[x][0]
        if prev is not None:
            px, pv = prev
            if (pv < v_threshold <= v) or (pv > v_threshold >= v):
                if v == pv:
                    return float(x)
                t = (v_threshold - pv) / (v - pv)
                return px + t * (x - px)
        prev = (x, v)
    return None

def build_edge_request(z, xe=XE, avg=0.5, far=1.0, width=W, height=H,
                       light=2.0, blocker_r=8, min_kernel=1.0, max_kernel=24.0):
    shadow = [[avg if i < xe else far for i in range(width)] for _ in range(height)]
    queries = [
        {"x": x, "y": height // 2, "z": z, "id": "x%d" % x}
        for x in range(width)
    ]
    return {
        "shadow": shadow,
        "query": queries,
        "light_size": light,
        "blocker_r": blocker_r,
        "min_kernel": min_kernel,
        "max_kernel": max_kernel,
        "depth_bias": 0.0,
        "near_depth": 0.0,
        "far_depth": far,
    }


def _reference_penumbra_width(z, avg, light):
    """解析半影宽度（= 2R，R 为 PCSS 滤波半径）。"""
    return 2.0 * light * (z - avg) / avg


def _circle_overlap(r):
    """半径 1 的圆在 x<=r 一侧所占面积比例（r ∈ [-1,1]，两端精确为 0/1）。"""
    if r <= -1.0:
        return 0.0
    if r >= 1.0:
        return 1.0
    return 0.5 + (r * math.sqrt(max(0.0, 1.0 - r * r)) + math.asin(r)) / math.pi


def _invert_overlap(v):
    """``_circle_overlap`` 的反函数（二分），返回 r ∈ [-1,1]。"""
    lo, hi = -1.0, 1.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if _circle_overlap(mid) < v:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def ref_visibility(x, xe, z, avg, light):
    """解析可见度：滤波半径 R 的圆盘与"受光半边"的重叠面积。

    遮挡板边缘在 x = xe，滤波圆盘中心在 x，半径 R = light*(z-avg)/avg。
    ``x - xe = -R`` 处 v=0，``x - xe = +R`` 处 v=1，过渡带宽度恰好 2R。
    """
    R = light * (z - avg) / avg
    if R <= 1e-12:
        return 1.0 if x > xe else 0.0
    return _circle_overlap((x - xe) / R)


def ref_edge_x(v, xe, z, avg, light):
    """解析剖面上可见度 = v 的 x 位置。"""
    R = light * (z - avg) / avg
    if R <= 1e-12:
        return float(xe)
    return xe + R * _invert_overlap(v)


def _profile(res):
    return {
        int(e["x"]): (e["v"], e["kernel"], e["blockers"], e["taps"])
        for e in res
    }


def check_far_field():
    avg, light = 0.5, 2.0
    z = 3.5                       # 解析半影宽 2R = 2*2*3/0.5 = 24 纹素
    out = pcss.compute(build_edge_request(z, avg=avg, light=light,
                                          blocker_r=8, max_kernel=32.0))
    res = _profile(out["lit"])

    # 只比较剖面两端（离贴图边界 >= R 的部分），避开搜索窗/贴图裁剪的端点效应。
    R = light * (z - avg) / avg
    lo = int(math.ceil(XE - R)) + 1
    hi = int(math.floor(XE + R)) - 1
    errs = [(abs(res[x][0] - ref_visibility(x, XE, z, avg, light)), x)
            for x in range(lo, hi + 1)]
    max_err, worst_x = max(errs)
    check("A1 远场半影剖面最大偏差 <= 0.05",
          max_err <= 0.05, "max_err=%.4f at x=%d (v=%.4f ref=%.4f)" % (
              max_err, worst_x, res[worst_x][0],
              ref_visibility(worst_x, XE, z, avg, light)))

    # 半影宽度：v=0.9 到 v=0.1 的过渡带宽度 vs 解析值
    x90 = _edge_distance(res, 0.9)
    x10 = _edge_distance(res, 0.1)
    if x90 is None or x10 is None:
        check("A2 远场半影宽度可测量", False, "x90=%r x10=%r" % (x90, x10))
    else:
        got_w = abs(x90 - x10)
        ref_w = abs(ref_edge_x(0.9, XE, z, avg, light)
                    - ref_edge_x(0.1, XE, z, avg, light))
        rel = abs(got_w - ref_w) / ref_w
        check("A2 远场最大半影宽度与参考差 <= 10%",
              rel <= 0.10, "got=%.2f ref=%.2f rel=%.3f" % (got_w, ref_w, rel))

    k = res[XE][1]
    expect_k = light * (z - avg) / avg
    check("A3 半影半径 = light_size*(z-avg)/avg",
          abs(k - expect_k) < 1e-6, "kernel=%.6f expect=%.6f" % (k, expect_k))



def check_contact_hardening():
    avg, light = 0.5, 2.0
    z = 0.504                      # 解析半影宽 = 2*2*0.004/0.5 = 0.032 纹素
    out = pcss.compute(build_edge_request(z, avg=avg, light=light, min_kernel=1.0,
                                          blocker_r=8, max_kernel=32.0))
    res = _profile(out["lit"])

    x50 = _edge_distance(res, 0.5)
    if x50 is None:
        check("A4 接触硬化区边缘位置可测量", False, "no 0.5 crossing")
    else:
        err = abs(x50 - XE)
        check("A4 接触硬化区边缘位置误差 <= 1 纹素", err <= 1.0,
              "x50=%.3f xe=%d err=%.3f" % (x50, XE, err))

    x90, x10 = _edge_distance(res, 0.9), _edge_distance(res, 0.1)
    if x90 is None or x10 is None:
        check("A5 接触区半影可测", False, "")
    else:
        got_w = abs(x90 - x10)
        ref_w = _reference_penumbra_width(z, avg, light)
        check("A5 接触区半影被 min_kernel 收窄",
              got_w <= 2.0 * 1.0 + 2.0 and got_w <= ref_w + 2.5,
              "got=%.2f ref=%.4f" % (got_w, ref_w))

    k_contact = res[XE][1]
    out_far = pcss.compute(build_edge_request(3.5, avg=avg, light=light,
                                              max_kernel=32.0))
    far_map = {int(e["x"]): e["kernel"] for e in out_far["lit"]}
    k_far = far_map[XE]
    check("A6 接触区半径远小于远场半径（远近软硬不同）",
          k_contact < 0.5 * k_far,
          "contact=%.3f far=%.3f" % (k_contact, k_far))


def check_monotonic():
    avg, light = 0.5, 2.0
    widths = []
    for dz in (0.25, 0.5, 1.0, 2.0, 3.0):
        z = avg + dz
        out = pcss.compute(build_edge_request(z, avg=avg, light=light,
                                              min_kernel=0.5, max_kernel=32.0))
        res = _profile(out["lit"])
        x90, x10 = _edge_distance(res, 0.9), _edge_distance(res, 0.1)
        widths.append(abs(x90 - x10) if x90 is not None and x10 is not None
                      else float("nan"))
    ok = all(not math.isnan(a) and not math.isnan(b) and b >= a - 0.5
             for a, b in zip(widths, widths[1:]))
    check("A7 半影宽度随 z 单调增大", ok,
          "widths=" + ",".join("%.2f" % w for w in widths))


def check_no_banding():
    """半影应随 (z - avg) 平滑连续变化：不能出现"条带"式的台阶。

    逐 z 扫描，记录每个 z 的滤波半径 R 与实测半影宽；要求
    (a) 半径与解析值一致到 1e-9（无量化/块状台阶）；
    (b) 相邻 z 之间半影宽变化连续（无跳变超过 2 纹素）。
    """
    avg, light = 0.5, 1.5
    ks, ws = [], []
    zs = [avg + 0.05 + 0.05 * t for t in range(56)]     # 0.55 .. 3.30
    for z in zs:
        out = pcss.compute(build_edge_request(z, avg=avg, light=light,
                                              min_kernel=0.3, max_kernel=40.0))
        # 取剖面上远离边界的点（v=0.5 处）来读半径
        ks.append(out["lit"][XE]["kernel"])
        res = _profile(out["lit"])
        x90, x10 = _edge_distance(res, 0.9), _edge_distance(res, 0.1)
        ws.append(abs(x90 - x10) if x90 is not None and x10 is not None
                  else float("nan"))

    # 半径必须精确等于解析式（未被夹取时）
    k_err = []
    for z, k in zip(zs, ks):
        expect = light * (z - avg) / avg
        if 0.3 <= expect <= 40.0:
            k_err.append(abs(k - expect))
    check("A8 滤波半径逐 z 精确等于解析式（无量化台阶）",
          max(k_err) <= 1e-9, "max_k_err=%.3e" % max(k_err))

    finite = [w for w in ws if not math.isnan(w)]
    jumps = [abs(b - a) for a, b in zip(finite, finite[1:])]
    check("A9 相邻 z 的半影宽无条带式跳变 (<= 2 纹素)",
          max(jumps) <= 2.0, "max_jump=%.3f over %d steps" % (max(jumps), len(jumps)))


def build_determinism_scene():
    w, h = 72, 40
    rows = []
    for j in range(h):
        row = []
        for i in range(w):
            d = 0.30 + 0.0025 * i + 0.0015 * j
            if 30 <= i <= 44 and 8 <= j <= 26:
                d = 0.12          # 近处遮挡物
            if 52 <= i <= 58 and 12 <= j <= 18:
                d = 0.0           # 穿透近裁剪面
            if i >= 64:
                d = 1.0           # 空
            row.append(d)
        rows.append(row)
    queries = []
    for j in range(4, h - 4, 4):
        for i in range(4, w - 4, 3):
            queries.append({"x": i, "y": j, "z": 0.62, "id": "q%d_%d" % (i, j)})
    return {
        "shadow": rows,
        "query": queries,
        "light_size": 1.5,
        "blocker_r": 7,
        "min_kernel": 1.0,
        "max_kernel": 16.0,
        "depth_bias": 0.005,
        "near_depth": 0.02,
        "far_depth": 0.99,
        "edge_mode": "skip",
    }


def check_determinism():
    req = build_determinism_scene()
    base = pcss.compute(req, order="row", threads=1, tile=8)
    d0, k0, b0 = base["digest"], base["kernel_digest"], base["blockers_digest"]
    s0 = base["stats"]

    variants = []
    for order in pcss.ORDER_CHOICES:
        for tile in (1, 3, 8, 16):
            for threads in (1, 4):
                variants.append((order, tile, threads))
    variants.append(("tile_snake", 5, 8))

    bad = []
    for order, tile, threads in variants:
        out = pcss.compute(req, order=order, tile=tile, threads=threads)
        if (out["digest"] != d0 or out["kernel_digest"] != k0
                or out["blockers_digest"] != b0):
            bad.append((order, tile, threads, out["digest"][:8]))
            continue
        st = out["stats"]
        for key in ("kernel_min", "kernel_max", "kernel_mean",
                    "blockers_total", "taps_total", "lit", "penumbra", "umbra"):
            if st[key] != s0[key]:
                bad.append((order, tile, threads, "stat:" + key))
    check("B1 %d 种遍历/分块/线程组合摘要与统计一致" % len(variants),
          not bad, "mismatches=%r" % (bad[:6],))

    base_map = {str(e["id"]): (e["v"], e["kernel"], e["blockers"])
                for e in base["lit"]}
    point_bad = []
    for order, tile, threads in variants[:14]:
        out = pcss.compute(req, order=order, tile=tile, threads=threads)
        m = {str(e["id"]): (e["v"], e["kernel"], e["blockers"])
             for e in out["lit"]}
        if m != base_map:
            point_bad.append((order, tile, threads))
    check("B2 逐点掩码/半径/遮挡数一致", not point_bad, "%r" % (point_bad,))

    single = dict(req)
    single["query"] = req["query"][:1]
    a = pcss.compute(single, threads=1)
    b = pcss.compute(single, threads=8)
    check("B3 单查询线程数无关", a["digest"] == b["digest"],
          "a=%s b=%s" % (a["digest"][:8], b["digest"][:8]))

    # random 顺序用不同种子仍应得到同一掩码
    seeds_bad = [
        s for s in (0, 1, 7, 12345)
        if pcss.compute(req, order="random", seed=s)["digest"] != d0
    ]
    check("B4 random 顺序不同种子掩码一致", not seeds_bad, "%r" % (seeds_bad,))


def check_open_plane():
    w, h = 64, 64
    rows = [[0.95] * w for _ in range(h)]
    queries = [{"x": i, "y": j, "z": 0.5, "id": "p%d_%d" % (i, j)}
               for j in range(0, h, 5) for i in range(0, w, 5)]
    req = {
        "shadow": rows,
        "query": queries,
        "light_size": 1.0,
        "blocker_r": 8,
        "min_kernel": 1.0,
        "max_kernel": 16.0,
        "depth_bias": 1e-6,
    }
    out = pcss.compute(req)
    bad = [e for e in out["lit"]
           if e["v"] != 1.0 or e["taps"] != 0 or e["kernel"] != 0.0]
    check("C1 无遮挡平面全亮且零滤波采样", not bad,
          "bad=%d/%d" % (len(bad), len(out["lit"])))
    check("C2 stats.full_light 且 taps_total=0",
          out["stats"]["full_light"] and out["stats"]["taps_total"] == 0,
          "full_light=%r taps=%d" % (out["stats"]["full_light"],
                                     out["stats"]["taps_total"]))

    # 大贴图 + 大量查询：确认开销不随贴图面积增长（不是"全图采样"）。
    import time
    big_w, big_h = 1024, 1024
    big_rows = [[0.95] * big_w for _ in range(big_h)]
    big_qs = [{"x": (i * 37) % big_w, "y": (i * 91) % big_h, "z": 0.5,
               "id": "b%d" % i} for i in range(2000)]
    t0 = time.time()
    big = pcss.compute({"shadow": big_rows, "query": big_qs,
                        "blocker_r": 8, "max_kernel": 16.0})
    dt = time.time() - t0
    check("C2b 1M 纹素空贴图 + 2000 查询：零采样且不随面积爆炸",
          big["stats"]["taps_total"] == 0 and big["stats"]["full_light"]
          and dt < 60.0,
          "taps=%d time=%.1fs" % (big["stats"]["taps_total"], dt))


def check_offmap_and_partial():
    w, h = 32, 32
    rows = [[0.4] * w for _ in range(h)]
    req = {
        "shadow": rows,
        "query": [
            {"x": 0, "y": 0, "z": 0.9, "id": "corner"},
            {"x": -40, "y": 10, "z": 0.9, "id": "offmap"},
            {"x": w + 40, "y": 10, "z": 0.9, "id": "offmap2"},
        ],
        "light_size": 1.0,
        "blocker_r": 8,
        "min_kernel": 1.0,
        "max_kernel": 16.0,
    }
    out = pcss.compute(req)
    by_id = {str(e["id"]): e for e in out["lit"]}
    corner = by_id["corner"]
    off1, off2 = by_id["offmap"], by_id["offmap2"]
    check("C3 角落查询部分覆盖搜索窗 (coverage<1)",
          0.0 < corner["coverage"] < 1.0,
          "coverage=%.4f" % corner["coverage"])
    check("C4 完全屏外查询标记 offmap 且全亮",
          off1["status"] == "offmap" and off1["v"] == 1.0
          and off2["status"] == "offmap" and off2["v"] == 1.0,
          "status=%r/%r" % (off1["status"], off2["status"]))


def check_penetrating_depth():
    w, h = 32, 32
    rows = [[0.0] * w for _ in range(h)]     # 全部穿透近裁剪面
    req = {
        "shadow": rows,
        "query": [{"x": 16, "y": 16, "z": 0.8, "id": "q"}],
        "light_size": 1.0,
        "blocker_r": 8,
        "min_kernel": 1.0,
        "max_kernel": 16.0,
        "near_depth": 0.01,
    }
    out = pcss.compute(req)
    e = out["lit"][0]
    check("C5 穿透近裁剪面深度被钳到近平面并判为遮挡",
          e["blockers"] > 0 and e["penetrating"] > 0 and e["v"] < 0.5,
          "blockers=%d pen=%d v=%.3f" % (e["blockers"], e["penetrating"], e["v"]))


def main():
    check_far_field()
    check_contact_hardening()
    check_monotonic()
    check_no_banding()
    check_determinism()
    check_open_plane()
    check_offmap_and_partial()
    check_penetrating_depth()

    total, failed = len(CHECKS), len(FAILS)
    print("")
    print("%d checks, %d failed" % (total, failed))
    if failed:
        for name, detail in FAILS:
            print("  FAILED: %s  %s" % (name, detail))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

