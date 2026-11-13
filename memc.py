#!/usr/bin/env python3
"""遮挡感知运动补偿插帧 (occlusion-aware MEMC).

算法流程:
1. 分层双向块匹配: 对 frame0->frame1 (f01) 与 frame1->frame0 (f10) 分别
   建 2 倍下采样金字塔, 每层在预测位移附近做 ±radius 的 SAD 块匹配
   (契约半径 8), 粗->细细化, 从而覆盖大于半径的运动。
2. 置信度: 锚块纹理能量 (MAD)、最优/次优假设间隔、绝对匹配代价三者
   联合判定; 低纹理与重复纹理块标记为低置信。低置信块按亮度相似性从
   置信邻居做确定性区域生长填充 (传播置信)。
3. 前后向一致性: 逐像素检查 f01(p) ≈ -f10(p+f01(p)) (切比雪夫容差 1),
   不一致或出界 -> 几何遮挡。
4. 合成: 一致且双侧有效 -> 双向加权; 否则按可见侧规则单向补偿
   (反向有效取 I1, 否则前向有效取 I0, 皆无效帧复制 I0)。亚像素双线性采样。

纯标准库。CLI 保持: python3 memc.py request.json outdir
环境变量 MEMC_ORDER_SEED=<int>: 随机重排各层块匹配遍历顺序 (仅用于自测
顺序无关性; 所有选择均基于确定性键, 结果与默认遍历完全一致)。
"""
import json
import math
import os
import random
import sys

# ---- 置信度阈值 (灰度 0..255) ----
TEX_MIN = 2.0       # 锚块平均绝对偏差低于此值视为低纹理
COST_MAX = 20.0     # 最优匹配 MAD 高于此值视为匹配不可信
MARGIN_MIN = 0.15   # 最优/次优位移相对间隔低于此值视为重复纹理歧义
ZERO_COST_MAX = 3.0  # 零位移假设 MAD 低于此值时, 低纹理/重复区按静态判可信
FILL_DIFF = 25.0    # 低置信区域生长允许的块均值差
CONS_TOL = 1        # 前后向一致性容差 (像素, 切比雪夫距离)

# 决策码
DEC_BIDIR = 0
DEC_I1 = 1
DEC_I0 = 2
DEC_COPY = 3


# ---------------- PGM IO ----------------
def read_pgm(path):
    with open(path) as f:
        tok = f.read().split()
    assert tok[0] == "P2", "only P2 PGM supported"
    w, h, mx = int(tok[1]), int(tok[2]), int(tok[3])
    px = list(map(int, tok[4:4 + w * h]))
    return w, h, px  # 一维扁平数组


def write_pgm(path, w, h, px):
    with open(path, "w") as f:
        f.write(f"P2\n{w} {h}\n255\n")
        for y in range(h):
            f.write(" ".join(map(str, px[y * w:(y + 1) * w])) + "\n")


# ---------------- 金字塔 ----------------
def downsample(a, w, h):
    """2x2 均值下采样 (奇数宽高丢弃最后一行/列)。"""
    w2, h2 = w // 2, h // 2
    out = [0] * (w2 * h2)
    for y in range(h2):
        base = 2 * y * w
        ob = y * w2
        for x in range(w2):
            i = base + 2 * x
            out[ob + x] = (a[i] + a[i + 1] + a[i + w] + a[i + w + 1] + 2) // 4
    return out, w2, h2


def build_pyramid(a, w, h, levels):
    pyr = [(a, w, h)]
    for _ in range(1, levels):
        a, w, h = downsample(a, w, h)
        pyr.append((a, w, h))
    return pyr


def auto_levels(w, h):
    levels = 1
    while (w // (2 ** levels) >= 24 and h // (2 ** levels) >= 16) and levels < 4:
        levels += 1
    return levels


# ---------------- 块匹配 ----------------
def block_rects(aw, ah, blk):
    rects = []
    for by in range((ah + blk - 1) // blk):
        y0, y1 = by * blk, min((by + 1) * blk, ah)
        for bx in range((aw + blk - 1) // blk):
            x0, x1 = bx * blk, min((bx + 1) * blk, aw)
            rects.append((by, bx, y0, y1, x0, x1))
    return rects


def match_block(anchor, aw, ah, dst, dw, dh, idx_list, n_pix, candidates):
    """在 candidates 位移集合上做均值 SAD 匹配。

    返回 (dy, dx, cost, margin, zero_cost): cost 为块内有效像素平均绝对差,
    margin 为最优与显著不同 (切比雪夫距离>=2) 的次优假设之间的相对间隔。
    有效像素少于 50% 的候选直接丢弃, 避免边界少量像素造成假极小。
    选择键 (cost, 位移长度, dy, dx) 完全确定, 与候选遍历顺序无关。
    """
    best = None  # (key, dy, dx, cost)
    second = None
    zero_cost = None
    scored = []
    for dy, dx in candidates:
        sad, n = 0, 0
        for si in idx_list:
            y, x = divmod(si, aw)
            sx, sy = x + dx, y + dy
            if 0 <= sx < dw and 0 <= sy < dh:
                sad += abs(anchor[si] - dst[sy * dw + sx])
                n += 1
        if n * 2 < n_pix:
            continue
        cost = sad / n
        if dy == 0 and dx == 0:
            zero_cost = cost
        scored.append((dy, dx, cost))
        key = (cost, abs(dy) + abs(dx), dy, dx)
        if best is None or key < best[0]:
            best = (key, dy, dx, cost)
    if best is None:  # 理论上不会发生 (d=0 总有全部有效像素)
        return 0, 0, 255.0, 0.0, zero_cost
    _, bdy, bdx, bcost = best
    for dy, dx, cost in scored:
        if max(abs(dy - bdy), abs(dx - bdx)) < 2:
            continue
        if second is None or cost < second:
            second = cost
    margin = 1.0 if second is None else (second - bcost) / (second + 1e-9)
    return bdy, bdx, bcost, margin, zero_cost


def compute_flow(src, dst, w, h, block, radius, levels, shuffle_seed=None):
    """分层块匹配, 返回块网格上的流场与置信信息。

    返回 dict: nby, nbx, blk, dy[], dx[], conf[] (0 低置信/1 传播/2 直接),
    cost[], texture[], margin[], mean[]。数组按块行主序。
    """
    sp = build_pyramid(src, w, h, levels)
    dp = build_pyramid(dst, w, h, levels)
    prev = None  # 上一 (更粗) 层结果
    for lvl in range(levels - 1, -1, -1):
        a, aw, ah = sp[lvl]
        b, bw, bh = dp[lvl]
        # 各层保持相同物理尺寸的块, 保证粗层仍有足够统计样本
        blk = block
        rects = block_rects(aw, ah, blk)
        if shuffle_seed is not None:
            random.Random(shuffle_seed + lvl * 1009).shuffle(rects)
        n = len(rects)
        nbx = (aw + blk - 1) // blk
        nby = (ah + blk - 1) // blk
        dy = [0] * n
        dx = [0] * n
        cost = [0.0] * n
        tex = [0.0] * n
        margin = [0.0] * n
        means = [0.0] * n
        zero = [0.0] * n
        for (by, bx, y0, y1, x0, x1) in rects:
            bi = by * nbx + bx
            idx_list = [y * aw + x for y in range(y0, y1) for x in range(x0, x1)]
            n_pix = len(idx_list)
            vals = [a[i] for i in idx_list]
            mean = sum(vals) / n_pix
            texture = sum(abs(v - mean) for v in vals) / n_pix
            means[bi], tex[bi] = mean, texture
            if prev is None:
                cands = [(dyv, dxv) for dyv in range(-radius, radius + 1)
                         for dxv in range(-radius, radius + 1)]
            else:
                pnbx = prev["nbx"]
                pby, pbx = by // 2, bx // 2
                pi = min(pby, prev["nby"] - 1) * pnbx + min(pbx, pnbx - 1)
                pdy, pdx = prev["dy"][pi] * 2, prev["dx"][pi] * 2
                cands = [(pdy + dyv, pdx + dxv)
                         for dyv in range(-radius, radius + 1)
                         for dxv in range(-radius, radius + 1)]
            bdy, bdx, bcost, bmargin, bzero = match_block(
                a, aw, ah, b, bw, bh, idx_list, n_pix, cands)
            dy[bi], dx[bi], cost[bi], margin[bi] = bdy, bdx, bcost, bmargin
            zero[bi] = bzero if bzero is not None else bcost
        prev = {"dy": dy, "dx": dx, "cost": cost, "texture": tex,
                "margin": margin, "mean": means, "zero": zero,
                "blk": blk, "nby": nby, "nbx": nbx}

    # 最细层直接置信度:
    #  (1) 纹理充分、假设间隔显著、代价可信 -> 直接可信
    #  (2) 零位移假设代价极低 -> 低纹理/周期纹理静态区直接可信 (零运动最安全)
    n = len(dy)
    conf = [0] * n
    for i in range(n):
        if (tex[i] >= TEX_MIN and margin[i] >= MARGIN_MIN
                and cost[i] <= COST_MAX):
            conf[i] = 2
        elif zero[i] <= ZERO_COST_MAX and cost[i] <= COST_MAX:
            dy[i], dx[i] = 0, 0
            conf[i] = 2
            cost[i], margin[i] = zero[i], 1.0
    # 低置信块区域生长: 从置信邻居按亮度相似性确定性传播
    nby, nbx, blk = prev["nby"], prev["nbx"], prev["blk"]
    fdy, fdx = dy[:], dx[:]
    for _ in range(nby + nbx):  # 足够覆盖曼哈顿最远距离
        changed = False
        for bi in range(n):
            if conf[bi] != 0:
                continue
            by, bx = divmod(bi, nbx)
            best_nb = None
            for ny, nx in ((by - 1, bx), (by + 1, bx), (by, bx - 1), (by, bx + 1)):
                if not (0 <= ny < nby and 0 <= nx < nbx):
                    continue
                ni = ny * nbx + nx
                if conf[ni] == 0:
                    continue
                diff = abs(means[bi] - means[ni])
                if diff > FILL_DIFF:
                    continue
                key = (diff, -conf[ni], ni)
                if best_nb is None or key < best_nb[0]:
                    best_nb = (key, ni)
            if best_nb is not None:
                ni = best_nb[1]
                fdy[bi], fdx[bi] = dy[ni], dx[ni]
                conf[bi] = 1
                changed = True
        if not changed:
            break
    return {"nby": nby, "nbx": nbx, "blk": blk, "dy": fdy, "dx": fdx,
            "conf": conf, "cost": cost, "texture": tex, "margin": margin,
            "mean": means}


def flow_at(flow, x, y):
    """像素 (x,y) 所在块的流与置信级别; 出界返回 None。"""
    nbx, nby, blk = flow["nbx"], flow["nby"], flow["blk"]
    bx, by = x // blk, y // blk
    if not (0 <= bx < nbx and 0 <= by < nby):
        return None
    bi = by * nbx + bx
    return flow["dy"][bi], flow["dx"][bi], flow["conf"][bi]


# ---------------- 采样与合成 ----------------
def sample_bilinear(a, w, h, fx, fy):
    if fx < 0.0 or fy < 0.0 or fx > w - 1 or fy > h - 1:
        return None
    x0 = int(math.floor(fx))
    y0 = int(math.floor(fy))
    x1, y1 = min(x0 + 1, w - 1), min(y0 + 1, h - 1)
    wx, wy = fx - x0, fy - y0
    top = a[y0 * w + x0] * (1 - wx) + a[y0 * w + x1] * wx
    bot = a[y1 * w + x0] * (1 - wx) + a[y1 * w + x1] * wx
    return top * (1 - wy) + bot * wy


def interpolate(i0, i1, w, h, f01, f10, t):
    """返回 (插值图, 遮挡图, 低置信图, 决策图, 统计)。"""
    total = w * h
    out = [0] * total
    occl = [0] * total
    lowconf = [0] * total
    decision = [0] * total
    counts = {DEC_BIDIR: 0, DEC_I1: 0, DEC_I0: 0, DEC_COPY: 0}
    occl_px = lowconf_px = 0
    for y in range(h):
        for x in range(w):
            p = y * w + x
            r01 = flow_at(f01, x, y)
            r10 = flow_at(f10, x, y)
            d01y, d01x, c01 = r01
            d10y, d10x, c10 = r10
            if c01 == 0 or c10 == 0:
                lowconf[p] = 255
                lowconf_px += 1
            # 几何前后向一致性
            qx, qy = x + d01x, y + d01y
            rq = flow_at(f10, qx, qy)
            consistent = (rq is not None and
                          max(abs(d01x + rq[1]), abs(d01y + rq[0])) <= CONS_TOL)
            if not consistent:
                occl[p] = 255
                occl_px += 1
            # 两个采样位置
            s0 = sample_bilinear(i0, w, h, x - t * d01x, y - t * d01y)
            s1 = sample_bilinear(i1, w, h, x + (1 - t) * d10x,
                                 y + (1 - t) * d10y)
            v0, v1 = c01 > 0 and s0 is not None, c10 > 0 and s1 is not None
            if consistent and v0 and v1:
                val = (1 - t) * s0 + t * s1
                dec = DEC_BIDIR
            elif v1:  # 反向有效 -> 取可见侧 I1
                val, dec = s1, DEC_I1
            elif v0:  # 仅前向有效 -> I0 单向外推
                val, dec = s0, DEC_I0
            else:     # 两侧皆无效 -> 帧复制
                val, dec = float(i0[p]), DEC_COPY
            out[p] = max(0, min(255, int(round(val))))
            decision[p] = dec
            counts[dec] += 1
    stats = {"occl_px": occl_px, "lowconf_px": lowconf_px, "total_px": total,
             "occl_ratio": occl_px / total, "counts": counts}
    return out, occl, lowconf, decision, stats


# ---------------- 输出 ----------------
def flow_to_docs(flow):
    docs = []
    for by in range(flow["nby"]):
        for bx in range(flow["nbx"]):
            i = by * flow["nbx"] + bx
            docs.append({
                "by": by, "bx": bx,
                "dy": flow["dy"][i], "dx": flow["dx"][i],
                "conf": flow["conf"][i],
                "mad": round(flow["cost"][i], 4),
                "texture": round(flow["texture"][i], 4),
                "margin": round(flow["margin"][i], 4),
                "mean": round(flow["mean"][i], 2),
            })
    return docs


def main():
    req_path, outdir = sys.argv[1], sys.argv[2]
    os.makedirs(outdir, exist_ok=True)
    req = json.load(open(req_path))
    base = os.path.dirname(os.path.abspath(req_path))
    block = int(req.get("block", 8))
    radius = int(req.get("radius", 8))
    frames = []
    for fr in req["frames"][:2]:
        frames.append(read_pgm(os.path.join(base, fr["file"])))
    (w, h, i0), (w1, h1, i1) = frames
    assert (w, h) == (w1, h1), "frame size mismatch"
    levels = int(req.get("levels", auto_levels(w, h)))
    seed_env = os.environ.get("MEMC_ORDER_SEED")
    seed = int(seed_env) if seed_env else None

    f01 = compute_flow(i0, i1, w, h, block, radius, levels, seed)
    f10 = compute_flow(i1, i0, w, h, block, radius, levels, seed)

    n_blocks = f01["nbx"] * f01["nby"]
    direct_blocks = sum(1 for c in f01["conf"] if c == 2)
    propagated_blocks = sum(1 for c in f01["conf"] if c == 1)
    direct_blocks_r = sum(1 for c in f10["conf"] if c == 2)
    propagated_blocks_r = sum(1 for c in f10["conf"] if c == 1)

    with open(os.path.join(outdir, "flow_f01.json"), "w") as f:
        json.dump({"direction": "f01", "width": w, "height": h,
                   "block": block, "radius": radius, "levels": levels,
                   "vectors": flow_to_docs(f01)}, f, sort_keys=True, indent=1)
    with open(os.path.join(outdir, "flow_f10.json"), "w") as f:
        json.dump({"direction": "f10", "width": w, "height": h,
                   "block": block, "radius": radius, "levels": levels,
                   "vectors": flow_to_docs(f10)}, f, sort_keys=True, indent=1)

    per_t = {}
    first_stats = None
    for t in req["t"]:
        out, occl, lowconf, decision, st = interpolate(i0, i1, w, h, f01, f10, t)
        write_pgm(os.path.join(outdir, f"interp_{t}.pgm"), w, h, out)
        write_pgm(os.path.join(outdir, f"decision_{t}.pgm"), w, h, decision)
        if first_stats is None:
            first_stats = st
            write_pgm(os.path.join(outdir, "occlusion.pgm"), w, h, occl)
            write_pgm(os.path.join(outdir, "lowconf.pgm"), w, h, lowconf)
        per_t[str(t)] = {
            "decisions": {
                "bidirectional": st["counts"][DEC_BIDIR],
                "single_i1": st["counts"][DEC_I1],
                "single_i0": st["counts"][DEC_I0],
                "copy_i0": st["counts"][DEC_COPY],
            },
            "fallback_px": st["counts"][DEC_COPY],
        }

    stats = {
        # 契约字段
        "occl_ratio": first_stats["occl_ratio"],
        "fallback_px": first_stats["counts"][DEC_COPY],
        "mv_count": n_blocks,
        # 扩展统计
        "occl_px": first_stats["occl_px"],
        "lowconf_px": first_stats["lowconf_px"],
        "total_px": w * h,
        "mv_count_f01": n_blocks,
        "mv_count_f10": f10["nbx"] * f10["nby"],
        "direct_blocks_f01": direct_blocks,
        "propagated_blocks_f01": propagated_blocks,
        "direct_blocks_f10": direct_blocks_r,
        "propagated_blocks_f10": propagated_blocks_r,
        "params": {"block": block, "radius": radius, "levels": levels,
                   "consistency_tol": CONS_TOL},
        "per_t": per_t,
    }
    with open(os.path.join(outdir, "stats.json"), "w") as f:
        json.dump(stats, f, sort_keys=True, indent=2)


if __name__ == "__main__":
    main()
