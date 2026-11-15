#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析线段覆盖抗锯齿 (analytic coverage anti-aliasing).

逐像素覆盖 = 像素矩形与笔划图元(矩形 / 圆盘 / 圆扇形)的解析相交面积,
覆盖值作为 alpha 进入预乘 (premultiplied) src-over 合成.

边界约定 (全局一致, 与分块/遍历顺序无关):
  * 像素 (ix, iy) 为闭约定的正方形 [ix, ix+1] x [iy, iy+1], y 轴向上;
  * 画布为 [0, size] x [0, size], 图元部分出屏时按同一裁剪约定求交;
  * 整数平移只改变图元坐标, 浮点运算可精确往返, 结果逐比特一致.

折线 (共享端点的相邻段) 分解为内部互不相交的图元:
  * 每段的端帽矩形在公共顶点处按角平分半平面切割, 两端各归一侧;
  * 内角缺口由连接样式填充: round=圆盘扇形, bevel=三角形, miter=斜接三角;
  * 自由端点的端帽: butt=无, round=半圆盘, square=矩形外延 r.
因此共享端点的覆盖不会被重复计算; 不同链之间的相交最后统一 clamp 到 1.
"""
import json
import sys
import math
import hashlib

EPS = 1e-12

# ----------------------------------------------------------------------
# 向量小工具
# ----------------------------------------------------------------------
def sub(a, b):
    return (a[0] - b[0], a[1] - b[1])

def add(a, b):
    return (a[0] + b[0], a[1] + b[1])

def mul(a, k):
    return (a[0] * k, a[1] * k)

def dot(a, b):
    return a[0] * b[0] + a[1] * b[1]

def cross(a, b):
    return a[0] * b[1] - a[1] * b[0]

def norm(a):
    return math.hypot(a[0], a[1])

def unit(a):
    n = math.hypot(a[0], a[1])
    return (a[0] / n, a[1] / n) if n > EPS else (0.0, 0.0)

def perp_left(a):
    """左手法线: (x,y) -> (-y,x)."""
    return (-a[1], a[0])

# ----------------------------------------------------------------------
# 多边形半平面裁剪 (Sutherland-Hodgman), 保留 n . p >= c 的部分
# ----------------------------------------------------------------------
def clip_halfplane(poly, n, c):
    if not poly:
        return []
    out = []
    eps = EPS * max(1.0, abs(c))
    for i in range(len(poly)):
        cur = poly[i]
        nxt = poly[(i + 1) % len(poly)]
        fc = dot(n, cur) - c
        fn = dot(n, nxt) - c
        cin = fc >= -eps
        nin = fn >= -eps
        if cin:
            out.append(cur)
        if cin != nin:
            denom = dot(n, sub(nxt, cur))
            if abs(denom) <= EPS:
                continue
            t = (c - dot(n, cur)) / denom
            out.append((cur[0] + t * (nxt[0] - cur[0]),
                        cur[1] + t * (nxt[1] - cur[1])))
    return out

def clip_rect(poly, x0, y0, x1, y1):
    """多边形与轴对齐矩形求交 (四个半平面)."""
    poly = clip_halfplane(poly, (1.0, 0.0), x0)
    poly = clip_halfplane(poly, (-1.0, 0.0), -x1)
    poly = clip_halfplane(poly, (0.0, 1.0), y0)
    poly = clip_halfplane(poly, (0.0, -1.0), -y1)
    return poly

def polygon_area(poly):
    if len(poly) < 3:
        return 0.0
    s = 0.0
    for i in range(len(poly)):
        s += cross(poly[i], poly[(i + 1) % len(poly)])
    return abs(0.5 * s)

# ----------------------------------------------------------------------
# 圆盘与 (凸) 多边形的解析相交面积
#
# Green 定理: 面积 = 1/2 * 沿边界积分. 每条有向边按其与圆周的交点拆成
# 子区间, 圆内部分用直线三角形项 cross(p1,p2)/2, 圆外部分用圆弧项
# r^2 * atan2(cross(p1,p2), dot(p1,p2)) / 2. 对凸/非凸多边形均成立.
# ----------------------------------------------------------------------
def _circle_edge_contribution(ax, ay, bx, by, r):
    dx = bx - ax
    dy = by - ay
    A = dx * dx + dy * dy
    if A <= 1e-24:
        return 0.0
    B = 2.0 * (ax * dx + ay * dy)
    C = ax * ax + ay * ay - r * r
    disc = B * B - 4.0 * A * C
    ts = [0.0, 1.0]
    if disc >= 0.0:
        s = math.sqrt(max(0.0, disc))
        for t in ((-B - s) / (2.0 * A), (-B + s) / (2.0 * A)):
            if 1e-12 < t < 1.0 - 1e-12:
                ts.append(t)
    ts.sort()
    total = 0.0
    r2 = r * r
    for u, v in zip(ts, ts[1:]):
        if v - u <= 1e-15:
            continue
        x1 = ax + u * dx
        y1 = ay + u * dy
        x2 = ax + v * dx
        y2 = ay + v * dy
        mx = 0.5 * (x1 + x2)
        my = 0.5 * (y1 + y2)
        cr = x1 * y2 - y1 * x2
        if mx * mx + my * my <= r2:          # 子段中点在圆内 -> 直线项
            total += 0.5 * cr
        else:                                # 圆外 -> 圆弧项
            total += 0.5 * r2 * math.atan2(cr, x1 * x2 + y1 * y2)
    return total

def circle_polygon_area(c, r, poly):
    """圆盘 (c, r) 与多边形 poly 的相交面积 (绝对面积)."""
    if r <= EPS or len(poly) < 3:
        return 0.0
    cx, cy = c
    total = 0.0
    for i in range(len(poly)):
        a = poly[i]
        b = poly[(i + 1) % len(poly)]
        total += _circle_edge_contribution(a[0] - cx, a[1] - cy,
                                           b[0] - cx, b[1] - cy, r)
    return abs(total)

# ----------------------------------------------------------------------
# 渲染图元 (元组末元素统一为 bbox=(x0,y0,x1,y1), 用于像素/分块剔除):
#   ("rect",   多边形顶点, bbox)
#   ("disk",   中心, 半径, bbox)                  完整圆盘 (退化段/点)
#   ("sector", 中心, 半径, 起始角, 终止角, bbox)  CCW 扇形, 张角 <= pi
# ----------------------------------------------------------------------
def pixel_polygon(ix, iy):
    return [(float(ix), float(iy)), (float(ix + 1), float(iy)),
            (float(ix + 1), float(iy + 1)), (float(ix), float(iy + 1))]

def prim_bbox_hit(bb, ix, iy):
    return not (bb[2] <= ix or bb[0] >= ix + 1 or
                bb[3] <= iy or bb[1] >= iy + 1)

def prim_coverage(prim, ix, iy):
    kind = prim[0]
    px = pixel_polygon(ix, iy)
    if kind == "rect":
        return polygon_area(clip_rect(prim[1], ix, iy, ix + 1, iy + 1))
    if kind == "disk":
        return circle_polygon_area(prim[1], prim[2], px)
    # sector: 先把像素按扇形的两条边界射线半平面裁成楔形, 再与圆盘求交
    _, c, r, a0, a1, _ = prim
    d0 = (math.cos(a0), math.sin(a0))
    d1 = (math.cos(a1), math.sin(a1))
    # cross(d0, p-c) >= 0  ->  (-d0y, d0x) . p >= 该法向 . c
    n0 = (-d0[1], d0[0])
    n1 = (d1[1], -d1[0])          # cross(d1, p-c) <= 0
    poly = clip_halfplane(px, n0, dot(n0, c))
    poly = clip_halfplane(poly, n1, dot(n1, c))
    return circle_polygon_area(c, r, poly) if len(poly) >= 3 else 0.0

def rect_bbox(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return (min(xs), min(ys), max(xs), max(ys))

def disk_bbox(c, r):
    return (c[0] - r, c[1] - r, c[0] + r, c[1] + r)

# ----------------------------------------------------------------------
# 折线分解: 段矩形(角平分切割) + 连接填充 + 端帽
# ----------------------------------------------------------------------
def _segment_rect(p, q, r, extend):
    """段 p->q 的端帽矩形; extend 为两端沿方向的外延 (square 端帽用)."""
    v = sub(q, p)
    length = norm(v)
    if length <= EPS:
        return None
    d = (v[0] / length, v[1] / length)
    n = perp_left(d)
    if extend:
        p = sub(p, mul(d, extend))
        q = add(q, mul(d, extend))
    return [add(p, mul(n, r)), add(q, mul(n, r)),
            sub(q, mul(n, r)), sub(p, mul(n, r))]

def build_chain_primitives(points, radii, cap, join, miter_limit):
    """把一条折线分解为互不重叠的图元列表.

    points: 折线顶点; radii[i]: 段 i 的半宽 (段矩形用).
    """
    prims = []
    nseg = len(points) - 1
    if nseg == 0:
        r = radii[0]
        if r > EPS:
            c = points[0]
            prims.append(("disk", c, r, disk_bbox(c, r)))
        return prims

    dirs = []
    norms = []
    for i in range(nseg):
        d = unit(sub(points[i + 1], points[i]))
        dirs.append(d)
        norms.append(perp_left(d))

    # --- 段矩形, 在内部顶点按角平分半平面切割 ---
    for i in range(nseg):
        r = radii[i]
        if r <= EPS:
            continue
        extend = r if cap == "square" else 0.0
        poly = _segment_rect(points[i], points[i + 1], r, extend)
        if poly is None:
            # 零长度退化段: 直接渲染为完整圆盘 (端帽不再额外生成)
            c = points[i]
            prims.append(("disk", c, r, disk_bbox(c, r)))
            continue
        v = points[i]
        if i > 0:                                   # 起点(出段): 保留 b.(p-v)>=0
            # 角平分向量 b=d0+d1 在任意转弯方向都指向折线内侧;
            # 出段起点保留 b 的正向侧, 入段终点保留 b 的负向侧, 两区域不重叠.
            d0, d1 = dirs[i - 1], dirs[i]
            b = unit(add(d0, d1))
            if norm(b) <= EPS:                      # 近 180 度折返的兜底
                b = perp_left(d0)
            poly = clip_halfplane(poly, b, dot(b, v))
        if i < nseg - 1:                            # 终点(入段): 保留 b.(p-v)<=0
            d0, d1 = dirs[i], dirs[i + 1]
            b = unit(add(d0, d1))
            if norm(b) <= EPS:
                b = perp_left(d0)
            ve = points[i + 1]                      # 终点顶点, 切勿复用段起点
            # n=-b, c=-b.ve: n.p>=c 等价于 b.(p-ve)<=0
            poly = clip_halfplane(poly, (-b[0], -b[1]), -dot(b, ve))
        if len(poly) >= 3 and polygon_area(poly) > EPS:
            prims.append(("rect", poly, rect_bbox(poly)))

    # --- 内部顶点的连接填充 (内角缺口) ---
    for i in range(1, nseg):
        v = points[i]
        r0, r1 = radii[i - 1], radii[i]
        R = 0.5 * (r0 + r1)
        if R <= EPS:
            continue
        d0, d1 = dirs[i - 1], dirs[i]
        n0, n1 = norms[i - 1], norms[i]
        turn = math.atan2(cross(d0, d1), dot(d0, d1))
        if abs(turn) <= EPS:
            continue                                # 直线: 无缺口
        if join == "round":
            # 内角缺口扇形, 张角恒为 |turn| (数值扫描验证, 任意角度成立):
            #   左转弯: 从 -n0 CCW 到 -n1;
            #   右转弯: 从  n1 CCW 到  n0.
            span = abs(turn)
            if turn > 0:
                start = (-n0[0], -n0[1])
            else:
                start = n1
            a0 = math.atan2(start[1], start[0])
            prims.append(("sector", v, R, a0, a0 + span,
                          disk_bbox(v, R)))
        else:
            e0 = sub(v, mul(n0, r0))                # 入段内侧边端点
            e1 = add(v, mul(n1, r1))               # 出段内侧边端点
            fill = None
            if join == "miter":
                denom = cross(d0, d1)
                if abs(denom) > EPS:
                    w = sub(e1, e0)
                    t = cross(w, d1) / denom
                    miter = add(e0, mul(d0, t))
                    if norm(sub(miter, v)) <= miter_limit * R + EPS:
                        # 斜接四边形: 两内侧边端点 -> 顶点 -> 斜交点
                        fill = [e0, v, e1, miter]
                if fill is None:                    # 超限/平行 -> 回退 bevel
                    fill = [e0, v, e1]
            else:                                   # bevel
                fill = [e0, v, e1]
            if len(fill) >= 3 and polygon_area(fill) > EPS:
                prims.append(("rect", fill, rect_bbox(fill)))

    # --- 自由端帽 (零长度退化段已作为完整圆盘处理, 跳过) ---
    if radii[0] > EPS and norm(sub(points[1], points[0])) > EPS:
        _append_cap(prims, points[0], dirs[0], radii[0], cap, start=True)
    last = nseg - 1
    if radii[last] > EPS and norm(sub(points[last + 1],
                                      points[last])) > EPS:
        _append_cap(prims, points[nseg], dirs[last],
                    radii[last], cap, start=False)
    return prims

def _append_cap(prims, v, d, r, cap, start):
    if cap in ("butt", "square"):                  # square 外延已并入段矩形
        return
    # round: 朝外的半圆盘 = 张角 pi 的扇形
    direction = (-d[0], -d[1]) if start else d
    base = math.atan2(direction[1], direction[0])
    a0 = base - 0.5 * math.pi
    a1 = base + 0.5 * math.pi
    prims.append(("sector", v, r, a0, a1, disk_bbox(v, r)))

# ----------------------------------------------------------------------
# 链组织: 按共享端点把线段串成折线
# ----------------------------------------------------------------------
def build_chains(segs):
    adj = {}
    for idx, s in enumerate(segs):
        p0 = (s["x0"], s["y0"])
        p1 = (s["x1"], s["y1"])
        adj.setdefault(p0, []).append((idx, 0))
        adj.setdefault(p1, []).append((idx, 1))

    used = [False] * len(segs)
    chains = []

    def walk(first):
        s = segs[first]
        pts = [(s["x0"], s["y0"]), (s["x1"], s["y1"])]
        ids = [first]
        used[first] = True
        while True:                                  # 向尾端延伸
            tail = pts[-1]
            nxt = None
            for cand, end in adj.get(tail, ()):
                if not used[cand]:
                    nxt = (cand, end)
                    break
            if nxt is None:
                break
            cand, end = nxt
            used[cand] = True
            ids.append(cand)
            cs = segs[cand]
            pts.append((cs["x1"], cs["y1"]) if end == 0
                       else (cs["x0"], cs["y0"]))
        while True:                                  # 向头端延伸
            head = pts[0]
            nxt = None
            for cand, end in adj.get(head, ()):
                if not used[cand]:
                    nxt = (cand, end)
                    break
            if nxt is None:
                break
            cand, end = nxt
            used[cand] = True
            ids.insert(0, cand)
            cs = segs[cand]
            pts.insert(0, (cs["x1"], cs["y1"]) if end == 0
                          else (cs["x0"], cs["y0"]))
        return pts, ids

    for idx in range(len(segs)):
        if not used[idx]:
            chains.append(walk(idx))
    return chains

# ----------------------------------------------------------------------
# 分块渲染
# ----------------------------------------------------------------------
def render(segs, size, tile=32, order="raster",
           cap="round", join="round", miter_limit=4.0):
    """返回 (cov, rgba_premul, primitives).

    cov: size x size 的几何覆盖 (0..1); rgba: 预乘 8-bit RGBA.
    分块/遍历顺序只影响计算调度, 每像素按全局固定图元顺序累加, 结果确定.
    """
    # 1) 全局图元 (固定规范顺序: 链顺序 -> 链内图元顺序)
    prim_items = []          # (prim, rgba01)
    for pts, ids in build_chains(segs):
        chain_segs = [segs[i] for i in ids]
        radii = []
        for s in chain_segs:
            w = float(s.get("w", 1.0))
            radii.append(0.0 if w <= 0.0 else 0.5 * w)
        prims = build_chain_primitives(pts, radii, cap, join, miter_limit)
        col = chain_segs[0].get("rgba", [255, 255, 255, 255])
        col01 = tuple(min(1.0, max(0.0, c / 255.0)) for c in col)
        prim_items.extend((p, col01) for p in prims)

    S = size
    cov = [[0.0] * S for _ in range(S)]
    out_r = [[0.0] * S for _ in range(S)]
    out_g = [[0.0] * S for _ in range(S)]
    out_b = [[0.0] * S for _ in range(S)]
    out_a = [[0.0] * S for _ in range(S)]

    cuts = list(range(0, S, max(1, min(tile, S))))
    if order == "raster":
        tile_coords = [(tx, ty) for ty in cuts for tx in cuts]
    else:                                       # column: 反向遍历顺序
        tile_coords = [(tx, ty) for tx in reversed(cuts)
                       for ty in reversed(cuts)]

    for tx, ty in tile_coords:
        tx1, ty1 = min(tx + tile, S), min(ty + tile, S)
        # 该分块命中的图元, 保持全局规范顺序
        local = [k for k, item in enumerate(prim_items)
                 if not (item[0][-1][2] <= tx or item[0][-1][0] >= tx1 or
                         item[0][-1][3] <= ty or item[0][-1][1] >= ty1)]
        if order == "raster":
            pixels = [(x, y) for y in range(ty, ty1) for x in range(tx, tx1)]
        else:
            pixels = [(x, y) for x in range(tx1 - 1, tx - 1, -1)
                      for y in range(ty1 - 1, ty - 1, -1)]
        for x, y in pixels:
            areas = []
            for k in local:
                p, col = prim_items[k]
                if prim_bbox_hit(p[-1], x, y):
                    a = prim_coverage(p, x, y)
                    if a > EPS:
                        areas.append((a, col))      # 保留原始面积, 不预截断
            # 几何覆盖: 固定顺序 fsum, 与调度无关; 多图元并集统一 clamp 到 1
            cov[y][x] = min(1.0, math.fsum(a for a, _ in areas))
            # 预乘 src-over 合成 (按同一规范顺序); src_a 同样 clamp 到 1
            A = 0.0
            R = G = B = 0.0
            for a, col in areas:
                sr, sg, sb, sa = col
                src_a = min(1.0, a) * sa
                if src_a <= EPS:
                    continue
                R += sr * src_a * (1.0 - A)
                G += sg * src_a * (1.0 - A)
                B += sb * src_a * (1.0 - A)
                A += src_a * (1.0 - A)
            out_r[y][x], out_g[y][x] = R, G
            out_b[y][x], out_a[y][x] = B, min(1.0, A)

    rgba = [[[quant(out_r[y][x]), quant(out_g[y][x]),
              quant(out_b[y][x]), quant(out_a[y][x])]
             for x in range(S)] for y in range(S)]
    return cov, rgba, prim_items

def quant(v):
    return int(min(255, max(0, v * 255.0 + 0.5)))

def run(req):
    segs = req["segs"]
    size = int(req["size"])
    tile = int(req.get("tile", 32))
    order = req.get("order", "raster")
    cap = req.get("cap", "round")
    join = req.get("join", "round")
    miter_limit = float(req.get("miter_limit", 4.0))
    cov, rgba, _prims = render(segs, size, tile, order,
                               cap, join, miter_limit)
    energy = math.fsum(cov[y][x] for y in range(size) for x in range(size))
    h = hashlib.sha256()
    h.update(bytes([size & 255, (size >> 8) & 255]))
    for row in rgba:
        for px in row:
            h.update(bytes(px))
    payload = dict(cov=cov, rgba=rgba, energy=energy)
    payload["digest"] = h.hexdigest()
    return payload

def main():
    with open(sys.argv[1]) as f:
        req = json.load(f)
    with open(sys.argv[2], "w") as f:
        json.dump(run(req), f)

if __name__ == "__main__":
    main()
