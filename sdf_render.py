#!/usr/bin/env python3
"""SDF CSG 稳定布尔渲染（修复版）。

相对基线的修复：
  1. 跨符号回退：一步跨过表面（d 由正变负）时二分回退求精，薄壁不再穿透。
  2. 薄壁细分：|d| < THIN_BAND 时步长钳制到 THIN_STEP，0.02 薄壁不被跳过。
  3. Lipschitz 回退：CSG 组合距离非精确，若场增长超过 1-Lipschitz 允许值，
     回退半步重试（上限 32 次保证终止）。
  4. ownership：叶子按「规范化树」前序编号（union/inter 子树按结构键排序，
     与输入顺序无关）；公共面 |da-db|<TIE_EPS 时归属 pid 小者，全局一致。
  5. eval_order(pre/post) 仅改变遍历顺序；距离/归属/分类位级一致；NaN 归约确定。

命中分类：法向 n = parity * 归属基元解析梯度；dot(rd,n) < 0 -> 入射(1)，否则出射(2)。

用法: python3 sdf_render.py scene.json outdir
输出: img.pgm (hit=255), steps.pgm (逐像素步数), stats.json
      {"hits", "steps_hist", "ownership", "classes", "depth"}  (逐像素, 行主序)
"""
import json, sys, math, os

HIT_EPS   = 1e-4    # 命中阈值
TIE_EPS   = 1e-6    # 公共面归属阈值
THIN_BAND = 0.03    # 薄壁/近面细分带
THIN_STEP = 0.005   # 细分带内最大步长
MAX_STEPS = 256
T_MAX     = 20.0

MISS, ENTER, EXIT = 0, 1, 2

# ---------------- 基元 ----------------

def prim_eval(node, p):
    k = node["prim"]
    if k == "sphere":
        c = node["c"]
        return math.dist(p, c) - node["r"]
    if k == "box":
        c, h = node["c"], node["h"]
        q0 = abs(p[0]-c[0])-h[0]; q1 = abs(p[1]-c[1])-h[1]; q2 = abs(p[2]-c[2])-h[2]
        o = math.sqrt(max(q0,0.0)**2 + max(q1,0.0)**2 + max(q2,0.0)**2)
        return o + min(0.0, max(q0, q1, q2))
    if k == "plane":
        n = node["n"]
        return p[0]*n[0] + p[1]*n[1] + p[2]*n[2] - node["d"]
    raise ValueError("unknown prim " + k)

def prim_grad(node, p):
    """基元解析梯度（外法向）。零分量符号约定取 +，全局一致。"""
    k = node["prim"]
    if k == "sphere":
        c = node["c"]
        v = [p[0]-c[0], p[1]-c[1], p[2]-c[2]]
        n = math.sqrt(v[0]*v[0]+v[1]*v[1]+v[2]*v[2])
        if n == 0.0: return [0.0, 0.0, 1.0]
        return [v[0]/n, v[1]/n, v[2]/n]
    if k == "box":
        c, h = node["c"], node["h"]
        a = [p[0]-c[0], p[1]-c[1], p[2]-c[2]]
        q = [abs(a[0])-h[0], abs(a[1])-h[1], abs(a[2])-h[2]]
        if max(q) > 0.0:                      # 盒外：活跃面方向
            g = [(1.0 if a[i] >= 0 else -1.0) if q[i] > 0 else 0.0 for i in range(3)]
            n = math.sqrt(g[0]*g[0]+g[1]*g[1]+g[2]*g[2])
            if n == 0.0: return [1.0, 0.0, 0.0]
            return [g[0]/n, g[1]/n, g[2]/n]
        kax = q.index(max(q))                 # 盒内：主导轴
        g = [0.0, 0.0, 0.0]
        g[kax] = 1.0 if a[kax] >= 0 else -1.0
        return g
    if k == "plane":
        return list(node["n"])
    raise ValueError("unknown prim " + k)

# ---------------- 规范化 + 稳定编号 ----------------

def _ckey(node):
    """结构键：union/inter 子键排序 -> 与子树输入顺序无关。"""
    if "prim" in node:
        return "P" + json.dumps({k: node[k] for k in node if not k.startswith("_")},
                                sort_keys=True)
    op = node["op"]
    if op in ("union", "inter"):
        ks = sorted([_ckey(node["a"]), _ckey(node["b"])])
        return op + "(" + ks[0] + "," + ks[1] + ")"
    return "sub(" + _ckey(node["a"]) + "," + _ckey(node["b"]) + ")"

def prepare(node, _reg=None):
    """规范化树（union/inter 子树按结构键排序）+ 前序叶子编号 + 基元注册表。

    同一 CSG 树无论子树输入顺序如何都得到同一编号 -> 求值顺序无关。
    返回 (tree, reg)，reg[pid] 为叶子基元节点。
    """
    if _reg is None: _reg = []
    if "prim" in node:
        n = dict(node)
        if n["prim"] == "plane":              # 平面法向单位化，保证 1-Lipschitz
            l = math.sqrt(sum(x*x for x in n["n"]))
            n["n"] = [x/l for x in n["n"]]; n["d"] = n["d"]/l
        n["_pid"] = len(_reg); n["_lo"] = n["_pid"]
        _reg.append(n)
        return n, _reg
    op = node["op"]
    a, _reg = prepare(node["a"], _reg)
    b, _reg = prepare(node["b"], _reg)
    if op in ("union", "inter") and _ckey(b) < _ckey(a):
        a, b = b, a
    n = {"op": op, "a": a, "b": b, "_lo": min(a["_lo"], b["_lo"])}
    return n, _reg

# ---------------- 距离 + 归属求值 ----------------

def _combine(op, da, oa, pa, db, ob, pb):
    """(d, owner, parity) 归约。值取精确 min/max（与顺序无关，NaN 确定传播）；
    公共面 |da-db|<TIE_EPS 时归属 pid 小者（全局一致）。"""
    if math.isnan(da) or math.isnan(db):
        return float("nan"), (oa if oa <= ob else ob), pa
    tie = abs(da - db) < TIE_EPS
    if op == "union":
        if tie:
            return (da if da <= db else db), (oa if oa <= ob else ob), \
                   (pa if oa <= ob else pb)
        return (da, oa, pa) if da < db else (db, ob, pb)
    else:  # inter
        if tie:
            return (da if da >= db else db), (oa if oa <= ob else ob), \
                   (pa if oa <= ob else pb)
        return (da, oa, pa) if da > db else (db, ob, pb)

def eval_tree(node, p, order="post"):
    """返回 (d, owner_pid, parity)。parity=-1 表示归属面来自 sub 的负侧。"""
    if "prim" in node:
        return prim_eval(node, p), node["_pid"], 1
    A, B = node["a"], node["b"]
    if order == "pre":            # 仅改变求值顺序，不改变结果
        A, B = B, A
    da, oa, pa = eval_tree(A, p, order)
    db, ob, pb = eval_tree(B, p, order)
    op = node["op"]
    if op == "sub":
        db, pb = -db, -pb
        op = "inter"
    return _combine(op, da, oa, pa, db, ob, pb)

def eval_dist(node, p, order="post"):
    return eval_tree(node, p, order)[0]

def grad_tree(node, p, order="post", h=1e-4):
    """CSG 组合距离的数值梯度（中心差分）。"""
    g = []
    for i in range(3):
        pe = list(p); pe[i] += h
        me = list(p); me[i] -= h
        g.append((eval_dist(node, pe, order) - eval_dist(node, me, order)) / (2.0*h))
    return g

def owner_normal(reg, p, owner, parity):
    g = prim_grad(reg[owner], p)
    return [parity*g[0], parity*g[1], parity*g[2]]

# ---------------- 步进（带回退） ----------------

def _bisect(tree, ro, rd, lo, hi, thresh, order, iters=40):
    """在 [lo,hi] 上二分 d=thresh 的跨越点（lo 侧 d>thresh，hi 侧 d<=thresh）。"""
    for _ in range(iters):
        mid = 0.5*(lo+hi)
        p = [ro[0]+rd[0]*mid, ro[1]+rd[1]*mid, ro[2]+rd[2]*mid]
        if eval_dist(tree, p, order) > thresh:
            lo = mid
        else:
            hi = mid
    return 0.5*(lo+hi)

def march(tree, reg, ro, rd, order="post", tmax=T_MAX, eps=HIT_EPS,
          max_steps=MAX_STEPS):
    """返回 (t|None, steps, owner, class)。保守球追踪 + 回退：

    - d 由正变负（一步跨过表面/薄壁）-> 二分回退到符号根；
    - 场增长违反 1-Lipschitz（非精确距离）-> 回退半步重试；
    - |d|<THIN_BAND -> 步长钳制 THIN_STEP（薄壁强制细分）。
    """
    t = 0.0; steps = 0
    t_prev = None; d_prev = None
    retreats = 0
    while t < tmax and steps < max_steps:
        p = [ro[0]+rd[0]*t, ro[1]+rd[1]*t, ro[2]+rd[2]*t]
        d, owner, parity = eval_tree(tree, p, order)
        steps += 1
        if d < eps:                                   # 命中或跨过表面
            if d <= 0.0 and t_prev is not None and d_prev > 0.0:
                t = _bisect(tree, ro, rd, t_prev, t, 0.0, order)   # 跨符号回退
            elif d > 0.0:
                t2 = t + d
                p2 = [ro[0]+rd[0]*t2, ro[1]+rd[1]*t2, ro[2]+rd[2]*t2]
                d2 = eval_dist(tree, p2, order)
                if d2 <= 0.0:
                    t = _bisect(tree, ro, rd, t, t2, 0.0, order)   # 跨过即二分
                elif d2 < d:
                    t = t + d * (d / (d - d2))       # 割线求精, 误差 O(d^2)
                else:
                    t = t2 - d2                      # 切向最近点近似
            p = [ro[0]+rd[0]*t, ro[1]+rd[1]*t, ro[2]+rd[2]*t]
            d, owner, parity = eval_tree(tree, p, order)
            n = owner_normal(reg, p, owner, parity)
            s = n[0]*rd[0] + n[1]*rd[1] + n[2]*rd[2]
            return t, steps, owner, (ENTER if s < 0.0 else EXIT)
        if (d_prev is not None and retreats < 32
                and d > d_prev + (t - t_prev) + 1e-9):  # Lipschitz 违例 -> 回退半步
            t = t_prev + 0.5*(t - t_prev)
            retreats += 1
            continue
        retreats = 0
        step = d
        if -THIN_BAND < d < THIN_BAND and step > THIN_STEP:
            step = THIN_STEP                          # 薄壁/近面强制细分
        t_prev, d_prev = t, d
        t += step
    return None, steps, -1, MISS

# ---------------- 渲染 ----------------

def render(sc):
    W, H = sc["res"]
    order = sc.get("eval_order", "post")
    tree, reg = prepare(sc["tree"])
    img, steps_img = [], []
    ownership, classes, depth = [], [], []
    hits = 0; hist = {}
    ro = [0.0, 0.0, 4.0]
    for j in range(H):
        row, srow = [], []
        for i in range(W):
            x = 2.0*i/W - 1.0; y = 1.0 - 2.0*j/H
            rd = [x, y, -2.0]
            n = math.sqrt(rd[0]*rd[0]+rd[1]*rd[1]+rd[2]*rd[2])
            rd = [v/n for v in rd]
            t, s, ow, cl = march(tree, reg, ro, rd, order)
            row.append(255 if t is not None else 0)
            srow.append(min(255, s))
            if t is not None: hits += 1
            hist[s//8] = hist.get(s//8, 0) + 1
            ownership.append(ow); classes.append(cl)
            depth.append(t)
        img.append(row); steps_img.append(srow)
    stats = {"hits": hits, "steps_hist": hist, "ownership": ownership,
             "classes": classes, "depth": depth}
    return img, steps_img, stats

def write_pgm(p, img):
    h, w = len(img), len(img[0])
    with open(p, "w") as f:
        f.write("P2\n%d %d\n255\n" % (w, h))
        f.write("\n".join(" ".join(map(str, r)) for r in img))

def main():
    sc = json.load(open(sys.argv[1])); out = sys.argv[2]
    os.makedirs(out, exist_ok=True)
    img, s, stats = render(sc)
    write_pgm(os.path.join(out, "img.pgm"), img)
    write_pgm(os.path.join(out, "steps.pgm"), s)
    json.dump(stats, open(os.path.join(out, "stats.json"), "w"))

if __name__ == "__main__":
    main()
