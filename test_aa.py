#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""aa.py 的验收测试 (纯标准库, 直接 `python3 test_aa.py` 运行).

覆盖:
  1. 几何原语解析面积的已知值/能量守恒;
  2. 高分辨率 supersample 参考 vs 解析覆盖, 逐像素误差 <= 1/64;
  3. 亚像素宽线段总能量 == 解析长度 * 宽度 (+端帽);
  4. 两种 tile x 两种遍历顺序, RGBA 逐像素严格一致;
  5. 重叠线段交点覆盖 <= 1;
  6. 整数平移 / 部分出屏的边界约定;
  7. 端帽 (butt/round/square) 与连接 (round/bevel/miter) 样式.
"""
import math
import aa

TOL = 1.0 / 64.0


# ----------------------------------------------------------------------
# 高分辨率 supersample 参考实现 (与渲染器独立的点采样真值)
# ----------------------------------------------------------------------
def _seg_dist(px, py, a, b):
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    l2 = dx * dx + dy * dy
    if l2 <= 1e-24:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _chain_distance(px, py, pts):
    return min(_seg_dist(px, py, pts[i], pts[i + 1])
               for i in range(len(pts) - 1))


def supersample_cov(segs, size, K, cap="round", join="round"):
    """每个像素 KxK 个采样点, 返回 size x size 的参考覆盖.

    与渲染器独立的点采样真值: 链上点到折线距离 <= r 判定 round 笔划;
    butt 模式下自由端点不外延, 内部顶点仍按 join 约定填充.
    """
    chains = aa.build_chains(segs)
    radii_of = {}
    for pts, ids in chains:
        for i in ids:
            radii_of[i] = 0.5 * float(segs[i].get("w", 1.0))
    cov = [[0.0] * size for _ in range(size)]
    step = 1.0 / K
    for iy in range(size):
        for ix in range(size):
            hit = 0
            for u in range(K):
                for v in range(K):
                    x = ix + (u + 0.5) * step
                    y = iy + (v + 0.5) * step
                    inside = False
                    for pts, ids in chains:
                        r = max(radii_of[i] for i in ids)
                        if cap == "butt":
                            # 自由端不外延: 点到折线的最近参数 t 须在 [0,1] 段内;
                            # 内部顶点的内角由相邻段的距离判定天然覆盖.
                            for i in ids:
                                s = segs[i]
                                a = (s["x0"], s["y0"])
                                b = (s["x1"], s["y1"])
                                if _seg_dist(x, y, a, b) <= r:
                                    ax, ay = a
                                    dx, dy = b[0] - ax, b[1] - ay
                                    l2 = dx * dx + dy * dy
                                    t = ((x - ax) * dx + (y - ay) * dy) / l2
                                    if -1e-9 <= t <= 1.0 + 1e-9:
                                        inside = True
                                        break
                        else:
                            if _chain_distance(x, y, pts) <= r:
                                inside = True
                        if inside:
                            break
                    hit += 1 if inside else 0
            cov[iy][ix] = hit / (K * K)
    return cov


def max_pixel_err(a, b):
    return max(abs(a[y][x] - b[y][x])
               for y in range(len(a)) for x in range(len(a)))


# ----------------------------------------------------------------------
# 1. 几何原语
# ----------------------------------------------------------------------
def test_primitives():
    px = aa.pixel_polygon(0, 0)
    # 圆盘全覆盖 / 全内含 / 角点四分之一圆 / 边中点半圆
    assert abs(aa.circle_polygon_area((0.5, 0.5), 5.0, px) - 1.0) < 1e-12
    assert abs(aa.circle_polygon_area((0.5, 0.5), 0.25, px)
               - math.pi * 0.0625) < 1e-12
    assert abs(aa.circle_polygon_area((0.0, 0.0), 1.0, px)
               - math.pi / 4) < 1e-12
    assert abs(aa.circle_polygon_area((0.5, 0.0), 0.5, px)
               - 0.5 * math.pi * 0.25) < 1e-12
    # 圆盘跨像素能量守恒
    r = 1.7
    total = sum(aa.circle_polygon_area((1.0, 1.0), r,
                aa.pixel_polygon(x, y))
                for x in range(-1, 3) for y in range(-1, 3))
    assert abs(total - math.pi * r * r) < 1e-10
    # 旋转矩形能量守恒
    d = (math.cos(math.pi / 4), math.sin(math.pi / 4))
    n = (-d[1], d[0])
    r = 0.15
    p, q = (-2.0, -2.0), (2.0, 2.0)
    L = math.hypot(4, 4)
    poly = [(p[0] + n[0] * r, p[1] + n[1] * r),
            (q[0] + n[0] * r, q[1] + n[1] * r),
            (q[0] - n[0] * r, q[1] - n[1] * r),
            (p[0] - n[0] * r, p[1] - n[1] * r)]
    tot = sum(aa.polygon_area(aa.clip_rect(poly, x, y, x + 1, y + 1))
              for x in range(-4, 5) for y in range(-4, 5))
    assert abs(tot - L * 0.3) < 1e-10
    # 扇形跨像素能量守恒
    a0 = 0.3
    a1 = a0 + math.radians(73)
    rr = 1.3
    prim = ("sector", (0.0, 0.0), rr, a0, a1, (-rr, -rr, rr, rr))
    tot = sum(aa.prim_coverage(prim, x, y)
              for x in range(-2, 2) for y in range(-2, 2))
    assert abs(tot - 0.5 * rr * rr * (a1 - a0)) < 1e-10
    print("[1] geometry primitives OK")


# ----------------------------------------------------------------------
# 2. supersample 参考: 已知方向/宽度的线段族, 逐像素 <= 1/64
# ----------------------------------------------------------------------
def test_supersample_family():
    size = 24
    families = [
        # 水平 / 垂直 / 45 度 / 135 度 / 任意角, 含亚像素宽
        [dict(x0=3.0, y0=7.0, x1=20.0, y1=7.0, w=1.0)],
        [dict(x0=11.0, y0=3.0, x1=11.0, y1=20.0, w=0.5)],
        [dict(x0=3.0, y0=3.0, x1=19.0, y1=19.0, w=0.25)],
        [dict(x0=3.0, y0=20.0, x1=20.0, y1=3.0, w=0.75)],
        [dict(x0=4.0, y0=5.0, x1=19.0, y1=14.0, w=0.4)],
        [dict(x0=4.0, y0=5.37, x1=19.0, y1=14.13, w=0.125)],
    ]
    K = 64
    for fi, segs in enumerate(families):
        ref = supersample_cov(segs, size, K, cap="round")
        got = aa.run(dict(segs=segs, size=size))["cov"]
        err = max_pixel_err(ref, got)
        assert err <= TOL, (fi, err)
        print("    family %d max pixel err %.5f (<= %.5f)" % (fi, err, TOL))
    print("[2] supersample reference OK")


def test_supersample_polyline():
    # L 形折线 round join: 验证连接/共享端点的覆盖正确性
    size = 16
    segs = [dict(x0=3.0, y0=3.0, x1=11.0, y1=3.0, w=0.8),
            dict(x0=11.0, y0=3.0, x1=11.0, y1=12.0, w=0.8)]
    K = 64
    ref = supersample_cov(segs, size, K)
    got = aa.run(dict(segs=segs, size=size))["cov"]
    err = max_pixel_err(ref, got)
    assert err <= TOL, err
    # 多转角折线 (锐角+钝角混合), round 连接
    z = [dict(x0=2.0, y0=2.0, x1=11.0, y1=2.0, w=0.7),
         dict(x0=11.0, y0=2.0, x1=6.0, y1=8.0, w=0.7),
         dict(x0=6.0, y0=8.0, x1=13.0, y1=8.0, w=0.7)]
    ref_z = supersample_cov(z, size, K)
    got_z = aa.run(dict(segs=z, size=size))["cov"]
    err_z = max_pixel_err(ref_z, got_z)
    assert err_z <= TOL, err_z
    # butt 端帽用单段验证 (无连接歧义): 两端平切, 能量严格 L*w
    single = [dict(x0=3.0, y0=9.0, x1=13.0, y1=9.0, w=0.6)]
    ref_b = supersample_cov(single, size, K, cap="butt")
    got_b = aa.run(dict(segs=single, size=size, cap="butt"))["cov"]
    assert max_pixel_err(ref_b, got_b) <= TOL
    print("[3] supersample polyline/join/butt OK, err %.5f / %.5f"
          % (err, err_z))


# ----------------------------------------------------------------------
# 3. 亚像素宽度总能量 == 解析长度 * 宽度 (+端帽)
# ----------------------------------------------------------------------
def test_subpixel_energy():
    size = 40
    for w in (0.05, 0.1, 0.25, 0.5, 0.9):
        ang = math.radians(33.7)
        L = 26.0
        x0, y0 = 6.0, 7.0
        x1 = x0 + L * math.cos(ang)
        y1 = y0 + L * math.sin(ang)
        segs = [dict(x0=x0, y0=y0, x1=x1, y1=y1, w=w)]
        e = aa.run(dict(segs=segs, size=size))["energy"]
        # round 端帽: 两个半圆 = 一个圆盘 pi (w/2)^2
        expect = L * w + math.pi * (w / 2.0) ** 2
        assert abs(e - expect) < 1e-7, (w, e, expect)
        # butt: 严格 L*w
        eb = aa.run(dict(segs=segs, size=size, cap="butt"))["energy"]
        assert abs(eb - L * w) < 1e-7, (w, eb)
    # 关键点: 亚像素宽线段不再整条消失 (缺陷基线 w<1 直接跳过)
    e = aa.run(dict(segs=[dict(x0=2.0, y0=10.0, x1=30.0, y1=10.0,
                               w=0.1)], size=size))["energy"]
    assert e > 2.0
    print("[4] subpixel energy conservation OK")


# ----------------------------------------------------------------------
# 4. 两种 tile x 两种遍历顺序: RGBA 逐像素严格一致
# ----------------------------------------------------------------------
def test_tile_order_determinism():
    segs = [dict(x0=2.0, y0=2.0, x1=13.0, y1=2.0, w=1.2),
            dict(x0=13.0, y0=2.0, x1=13.0, y1=13.0, w=1.2),
            dict(x0=5.0, y0=11.0, x1=15.0, y1=4.0, w=0.4)]
    size = 18
    base = aa.run(dict(segs=segs, size=size, tile=32, order="raster"))
    for tile in (4, 7):
        for order in ("raster", "column"):
            r = aa.run(dict(segs=segs, size=size, tile=tile, order=order))
            assert r["cov"] == base["cov"], (tile, order)
            assert r["rgba"] == base["rgba"], (tile, order)
            assert r["digest"] == base["digest"], (tile, order)
    print("[5] tile/order bit-exact determinism OK")


# ----------------------------------------------------------------------
# 5. 重叠交点覆盖 <= 1
# ----------------------------------------------------------------------
def test_overlap_clamp():
    size = 16
    segs = [dict(x0=1.0, y0=8.0, x1=15.0, y1=8.0, w=3.0),
            dict(x0=8.0, y0=1.0, x1=8.0, y1=15.0, w=3.0),
            dict(x0=2.0, y0=2.0, x1=14.0, y1=14.0, w=1.5)]
    r = aa.run(dict(segs=segs, size=size))
    worst = max(max(row) for row in r["cov"])
    assert worst <= 1.0 + 1e-12
    # 交点中心像素应被完全覆盖
    assert r["cov"][8][8] == 1.0
    # 同一条链自身的图元互不重叠 (能量无重复计): 单链能量 == 解析面积
    # 段长 10 与 9, w=1; 90 度连接: 两矩形各切去 0.125 (净 18.75),
    # 端帽圆盘 pi r^2, 内角 1/4 圆盘.
    chain = [dict(x0=2.0, y0=4.0, x1=12.0, y1=4.0, w=1.0),
             dict(x0=12.0, y0=4.0, x1=12.0, y1=13.0, w=1.0)]
    e = aa.run(dict(segs=chain, size=size))["energy"]
    expect = 18.75 + math.pi * 0.25 + 0.25 * math.pi * 0.25
    assert abs(e - expect) < 1e-9, (e, expect)
    print("[6] overlap clamp <= 1 and no shared-endpoint double count OK")


# ----------------------------------------------------------------------
# 6. 边界约定: 整数平移 / 部分出屏 / 退化段
# ----------------------------------------------------------------------
def test_boundary_conventions():
    size = 16
    # 整数平移: 几何逐像素一致 (圆三角求值有 1e-14 级舍入, 容差远小于 1/64)
    r0 = aa.run(dict(segs=[dict(x0=2.0, y0=3.0, x1=11.0, y1=8.0,
                               w=0.4)], size=size))
    r1 = aa.run(dict(segs=[dict(x0=6.0, y0=7.0, x1=15.0, y1=12.0,
                               w=0.4)], size=size))
    worst = 0.0
    for y in range(size):
        for x in range(size):
            sx, sy = x - 4, y - 4
            v0 = r0["cov"][sy][sx] if 0 <= sx < size and 0 <= sy < size else 0.0
            worst = max(worst, abs(v0 - r1["cov"][y][x]))
    assert worst < 1e-12, worst
    # 部分出屏: 画布边缘平切, 边缘列/行和为宽度 (无圆盘泄漏到屏外逻辑)
    r = aa.run(dict(segs=[dict(x0=-8.0, y0=9.0, x1=8.0, y1=9.0,
                               w=1.0)], size=size))
    assert abs(sum(r["cov"][y][0] for y in range(size)) - 1.0) < 1e-12
    assert abs(r["energy"] - (8.0 + 0.5 * math.pi * 0.25)) < 1e-9
    # 退化段 (零长度): 渲染为圆盘, 能量 pi r^2
    rd = aa.run(dict(segs=[dict(x0=8.0, y0=8.0, x1=8.0, y1=8.0,
                                w=0.6)], size=size))
    assert abs(rd["energy"] - math.pi * 0.3 ** 2) < 1e-9
    # 零宽度: 不产生覆盖
    rz = aa.run(dict(segs=[dict(x0=1.0, y0=1.0, x1=10.0, y1=10.0,
                                w=0.0)], size=size))
    assert rz["energy"] == 0.0
    print("[7] boundary conventions (translate/clip/degenerate) OK")


# ----------------------------------------------------------------------
# 7. 端帽与连接样式
# ----------------------------------------------------------------------
def test_caps_and_joins():
    size = 16
    base = [dict(x0=3.0, y0=8.0, x1=13.0, y1=8.0, w=1.0)]
    # butt: L*w ; square: (L+w)*w ; round: L*w + pi r^2
    assert abs(aa.run(dict(segs=base, size=size, cap="butt"))["energy"]
               - 10.0) < 1e-9
    assert abs(aa.run(dict(segs=base, size=size, cap="square"))["energy"]
               - 11.0) < 1e-9
    assert abs(aa.run(dict(segs=base, size=size, cap="round"))["energy"]
               - (10.0 + math.pi * 0.25)) < 1e-9
    # 三种连接样式能量关系: miter >= round >= bevel (90 度)
    segs = [dict(x0=3.0, y0=3.0, x1=11.0, y1=3.0, w=1.0),
            dict(x0=11.0, y0=3.0, x1=11.0, y1=11.0, w=1.0)]
    eb = aa.run(dict(segs=segs, size=size, join="bevel"))["energy"]
    er = aa.run(dict(segs=segs, size=size, join="round"))["energy"]
    em = aa.run(dict(segs=segs, size=size, join="miter"))["energy"]
    assert eb < er < em, (eb, er, em)
    # 镜像右转能量相同
    segs_r = [dict(x0=3.0, y0=11.0, x1=11.0, y1=11.0, w=1.0),
              dict(x0=11.0, y0=11.0, x1=11.0, y1=3.0, w=1.0)]
    for join in ("round", "bevel", "miter"):
        e1 = aa.run(dict(segs=segs, size=size, join=join))["energy"]
        e2 = aa.run(dict(segs=segs_r, size=size, join=join))["energy"]
        assert abs(e1 - e2) < 1e-9, join
    print("[8] cap/join styles OK")


def test_premultiplied_rgba():
    # 半透明红色线段: 预乘输出 R=A (红), G=B=0; A 等于覆盖*颜色 alpha
    size = 12
    segs = [dict(x0=2.0, y0=6.0, x1=10.0, y1=6.0, w=2.0,
                 rgba=[255, 0, 0, 128])]
    r = aa.run(dict(segs=segs, size=size))
    for y in range(size):
        for x in range(size):
            pr, pg, pb, pa = r["rgba"][y][x]
            assert pg == 0 and pb == 0
            assert 0 <= pr <= pa <= 255
    # 全覆盖像素的 alpha 约为 128
    assert abs(r["rgba"][6][6][3] - 128) <= 1
    print("[9] premultiplied RGBA compositing OK")


if __name__ == "__main__":
    test_primitives()
    test_supersample_family()
    test_supersample_polyline()
    test_subpixel_energy()
    test_tile_order_determinism()
    test_overlap_clamp()
    test_boundary_conventions()
    test_caps_and_joins()
    test_premultiplied_rgba()
    print("\nALL ACCEPTANCE TESTS PASSED")
