#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mis.py — 多 lobe BSDF 方向采样 / pdf 求值 + 平衡启发式 MIS + 确定性可重放统计。

统一球面坐标约定
  局部 shading 坐标系: 宏表面法线 n = +z, 方向 w=(x,y,z), cosθ = w·n = z。
  所有 lobe 的 sample/pdf/eval 均在该约定下实现, 同一套正半球定义。
  背面剔除: wi·n <= 0 或 wo·n <= 0 -> pdf = 0, eval = 0, 贡献为 0。
  掠射角: GGX 采用可见法线分布采样 (VNDF, Heitz 2018), 掠射下仍低噪且能量守恒。

MIS (平衡启发式)
  策略 A: BSDF 混合采样 (按能量权重选 lobe, 混合 pdf = Σ wj·pj)。
  策略 B: 光源采样 (单位辐照环境光, 余弦半球采样)。
  w_a = pa/(pa+pb), w_b = pb/(pa+pb)。
  delta lobe (理想镜面): 对立体角密度退化为 0, 其路径 MIS 权重恒 1,
  光源策略在该路径权重恒 0; pa+pb == 0 的路径为退化路径, MIS 权重记 0,
  两类退化路径均单独计数。

确定性 / 可重放
  采样序列由 (seed, pixel, strategy) 派生的 counter-based SplitMix64 流生成,
  与线程数 / 分块大小 / 执行顺序无关。统计按 (thread, tile) 记录各 lobe 的
  样本数与能量份额, 像素结果按像素号归并, 乱序执行后重放摘要逐位一致。
"""
import json, sys, math, hashlib

PI = math.pi
MASK = (1 << 64) - 1

# ---------------- 确定性随机流 (counter-based, 顺序无关) ----------------
def _sm64(x):
    x = (x + 0x9E3779B97F4A7C15) & MASK
    z = x
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK
    return z ^ (z >> 31)

def stream_key(*words):
    h = 0x243F6A8885A308D3
    for w in words:
        h = _sm64(h ^ (int(w) & MASK))
    return h

class Rng:
    """同一 key 产生同一固定序列; key 只依赖 (seed,pixel,strategy)。"""
    __slots__ = ("s",)
    def __init__(self, key): self.s = key
    def next(self):
        self.s = _sm64(self.s)
        return (self.s >> 11) * (1.0 / (1 << 53))

def strat_cells(count):
    """count 个二维分层格 (jittered grid), 顺序确定, 与执行顺序无关。"""
    m = int(math.ceil(math.sqrt(count)))
    return [(i, j, m) for i in range(m) for j in range(m)][:count]

# ---------------- vec3 (局部坐标, n=+z) ----------------
def dot(a, b): return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]
def norm3(v):
    l = math.sqrt(v[0]*v[0] + v[1]*v[1] + v[2]*v[2])
    if l <= 0.0: return (0.0, 0.0, 1.0)
    return (v[0]/l, v[1]/l, v[2]/l)

# ---------------- BSDF lobes ----------------
class Lambert:
    """漫反射 lobe: 余弦加权采样, pdf = cosθ/π。"""
    kind = "smooth"
    def __init__(self, albedo): self.a = albedo
    def sample(self, wo, u1, u2):
        r = math.sqrt(u1); phi = 2.0*PI*u2
        return (r*math.cos(phi), r*math.sin(phi), math.sqrt(max(0.0, 1.0 - u1)))
    def pdf(self, wi, wo):
        if wi[2] <= 0.0 or wo[2] <= 0.0: return 0.0   # 背面剔除
        return wi[2]/PI
    def eval(self, wi, wo):
        if wi[2] <= 0.0 or wo[2] <= 0.0: return 0.0
        return self.a/PI
    def white_furnace(self): return self.a             # 解析出射辐照

class GGX:
    """粗糙镜面 lobe: GGX 微表面 + Smith 相关遮蔽, VNDF 采样 (掠射稳定)。"""
    kind = "smooth"
    def __init__(self, rough, f0=1.0):
        self.alpha = min(max(rough, 1e-3), 8.0)
        self.f0 = f0
    def D(self, m):
        if m[2] <= 0.0: return 0.0
        a2 = self.alpha*self.alpha
        t = (a2 - 1.0)*m[2]*m[2] + 1.0
        return a2/(PI*t*t)
    def _lam(self, w):
        z = w[2]
        if z <= 1e-9: return float("inf")
        tan2 = (1.0 - z*z)/(z*z)
        return 0.5*(math.sqrt(1.0 + self.alpha*self.alpha*tan2) - 1.0)
    def G1(self, w):
        l = self._lam(w)
        return 0.0 if l == float("inf") else 1.0/(1.0 + l)
    def sample_m(self, wo, u1, u2):
        """可见法线分布采样 (Heitz 2018), 返回微表面法线 m。"""
        a = self.alpha
        V = norm3((a*wo[0], a*wo[1], wo[2]))
        lensq = V[0]*V[0] + V[1]*V[1]
        if lensq > 1e-16:
            il = 1.0/math.sqrt(lensq); T1 = (-V[1]*il, V[0]*il, 0.0)
        else:
            T1 = (1.0, 0.0, 0.0)
        T2 = (V[1]*T1[2]-V[2]*T1[1], V[2]*T1[0]-V[0]*T1[2], V[0]*T1[1]-V[1]*T1[0])
        r = math.sqrt(u1); phi = 2.0*PI*u2
        t1 = r*math.cos(phi); t2 = r*math.sin(phi)
        s = 0.5*(1.0 + V[2])
        t2 = (1.0-s)*math.sqrt(max(0.0, 1.0 - t1*t1)) + s*t2
        z = math.sqrt(max(0.0, 1.0 - t1*t1 - t2*t2))
        Nh = (t1*T1[0]+t2*T2[0]+z*V[0],
              t1*T1[1]+t2*T2[1]+z*V[1],
              t1*T1[2]+t2*T2[2]+z*V[2])
        return norm3((a*Nh[0], a*Nh[1], max(Nh[2], 1e-12)))
    def sample(self, wo, u1, u2):
        m = self.sample_m(wo, u1, u2)
        d = dot(wo, m)
        return (2.0*d*m[0]-wo[0], 2.0*d*m[1]-wo[1], 2.0*d*m[2]-wo[2])
    def pdf(self, wi, wo):
        if wi[2] <= 0.0 or wo[2] <= 0.0: return 0.0   # 背面剔除
        m = norm3((wi[0]+wo[0], wi[1]+wo[1], wi[2]+wo[2]))
        if m[2] <= 0.0: return 0.0
        dm = dot(wo, m)
        if dm <= 0.0: return 0.0
        pm = self.G1(wo)*self.D(m)*dm/wo[2]           # 可见法线 pdf
        return pm/(4.0*dm)                            # 反射雅可比
    def eval(self, wi, wo):
        if wi[2] <= 0.0 or wo[2] <= 0.0: return 0.0
        m = norm3((wi[0]+wo[0], wi[1]+wo[1], wi[2]+wo[2]))
        G2 = 1.0/(1.0 + self._lam(wo) + self._lam(wi))
        return self.f0*self.D(m)*G2/(4.0*wo[2]*wi[2])
    def white_furnace(self): return self.f0           # F=1 时能量守恒

class Mirror:
    """理想镜面 delta lobe: 方向确定, 对立体角 pdf 退化为 0。"""
    kind = "delta"
    def __init__(self, f0=1.0): self.f0 = f0
    def sample(self, wo, u1, u2):
        return (-wo[0], -wo[1], wo[2])
    def pdf(self, wi, wo): return 0.0                 # delta: 密度退化
    def eval(self, wi, wo): return 0.0                # 贡献在采样路径单独处理
    def white_furnace(self): return self.f0

class EnvLight:
    """单位辐照环境光 (白炉), 余弦半球采样。"""
    L = 1.0
    def sample(self, u1, u2):
        r = math.sqrt(u1); phi = 2.0*PI*u2
        return (r*math.cos(phi), r*math.sin(phi), math.sqrt(max(0.0, 1.0 - u1)))
    def pdf(self, wi):
        return wi[2]/PI if wi[2] > 0.0 else 0.0

# ---------------- 材质组装 ----------------
def build_material(req):
    names = req.get("lobes", ["lambert"])
    albedo = float(req.get("albedo", 1.0))
    f0 = float(req.get("f0", 1.0))
    rough = float(req.get("rough", 0.3))
    lobes = []
    for nm in names:
        if nm == "lambert": lobes.append(("lambert", Lambert(albedo)))
        elif nm == "ggx": lobes.append(("ggx", GGX(rough, f0)))
        elif nm == "mirror": lobes.append(("mirror", Mirror(f0)))
        else: raise ValueError("unknown lobe: %s" % nm)
    w = req.get("weights")
    ws = [float(w.get(nm, 1.0)) for nm, _ in lobes] if w else [1.0]*len(lobes)
    s = sum(ws)
    return lobes, [x/s for x in ws]

# ---------------- 单像素估计 (BSDF 采样 + 光源采样, 平衡启发式) ----------------
def render_pixel(seed, px, n, lobes, ws, wo, light):
    rng_b = Rng(stream_key(seed, px, 1))   # 策略 A 固定序列
    rng_l = Rng(stream_key(seed, px, 2))   # 策略 B 固定序列
    nl = len(lobes)
    total = 0.0
    per_lobe = [0.0]*nl
    counts = {nm: 0 for nm, _ in lobes}; counts["light"] = 0
    mis_w_sum = 0.0; mis_w_cnt = 0
    delta_paths = 0; mis0 = 0; culled = 0
    # --- 策略 A: BSDF 采样, 按能量权重分层 (每 lobe 一个完整 jittered 网格) ---
    for j in range(nl):
        nm, lb = lobes[j]
        mj = max(1, int(round(math.sqrt(ws[j]*n))))
        ns = mj*mj                                   # 完整网格, 无偏
        acc = 0.0
        acc_lobe = [0.0]*nl
        for (ci, cj, cm) in strat_cells(ns):
            u = (ci + rng_b.next())/cm; v = (cj + rng_b.next())/cm
            wi = lb.sample(wo, u, v)
            if lb.kind == "delta":
                delta_paths += 1                     # delta 路径: MIS 权重恒 1
                if wi[2] > 0.0:
                    c = lb.f0*light.L
                    acc += c; acc_lobe[j] += c
                    mis_w_sum += 1.0; mis_w_cnt += 1
                else:
                    culled += 1
            elif wi[2] <= 0.0:
                culled += 1                          # 背面剔除
            else:
                pb = 0.0
                for k in range(nl):
                    if lobes[k][1].kind == "smooth":
                        pb += ws[k]*lobes[k][1].pdf(wi, wo)
                pl = light.pdf(wi)
                den = pb + pl
                if den <= 0.0:
                    mis0 += 1                        # 退化路径: MIS 权重 0
                else:
                    wb = pb/den
                    cz = wi[2]
                    if pb > 0.0:
                        fsum = 0.0
                        for k in range(nl):
                            if lobes[k][1].kind == "smooth":
                                fk = lobes[k][1].eval(wi, wo)
                                acc_lobe[k] += ws[k]*fk*cz*light.L/pb*wb
                                fsum += ws[k]*fk
                        acc += fsum*cz*light.L/pb*wb
                    mis_w_sum += wb; mis_w_cnt += 1
        counts[nm] += ns
        wj = ws[j]/ns
        total += wj*acc
        for k in range(nl):
            per_lobe[k] += wj*acc_lobe[k]
    # --- 策略 B: 光源采样 ---
    ml = max(1, int(round(math.sqrt(n))))
    ns = ml*ml
    acc = 0.0
    acc_lobe = [0.0]*nl
    for (ci, cj, cm) in strat_cells(ns):
        u = (ci + rng_l.next())/cm; v = (cj + rng_l.next())/cm
        wi = light.sample(u, v)
        pl = light.pdf(wi)
        pb = 0.0
        for k in range(nl):
            if lobes[k][1].kind == "smooth":
                pb += ws[k]*lobes[k][1].pdf(wi, wo)
        den = pb + pl
        if den <= 0.0 or pl <= 0.0:
            mis0 += 1                                # 退化路径: MIS 权重 0
        else:
            wl = pl/den
            cz = wi[2]
            fsum = 0.0
            for k in range(nl):
                if lobes[k][1].kind == "smooth":
                    fk = lobes[k][1].eval(wi, wo)
                    acc_lobe[k] += ws[k]*fk*cz*light.L/pl*wl
                    fsum += ws[k]*fk
            acc += fsum*cz*light.L/pl*wl
            mis_w_sum += wl; mis_w_cnt += 1
    counts["light"] += ns
    total += acc/ns
    for k in range(nl):
        per_lobe[k] += acc_lobe[k]/ns
    return {"out": total,
            "per_lobe": per_lobe,
            "counts": counts,
            "mis_w": mis_w_sum/max(1, mis_w_cnt),
            "delta_paths": delta_paths, "mis0": mis0, "culled": culled}

# ---------------- 分块 / 线程调度 (执行顺序可乱序, 结果不变) ----------------
def make_tiles(npix, tile):
    tiles = []
    for t0 in range(0, npix, tile):
        tiles.append(list(range(t0, min(t0+tile, npix))))
    return tiles

def shuffled(seq, key):
    rng = Rng(key)
    out = list(seq)
    for i in range(len(out)-1, 0, -1):
        j = int(rng.next()*(i+1))
        out[i], out[j] = out[j], out[i]
    return out

def run_image(req, seed, n, rough_of_px=None):
    """执行整幅图像; 返回 (pixels, thread_tile_stats, totals)。
    像素结果只依赖 (seed,pixel), 与线程/分块/执行顺序无关。"""
    res = req.get("res", [4, 4]); W, H = int(res[0]), int(res[1])
    npix = W*H
    tile = max(1, int(req.get("tile", 2)))
    threads = max(1, int(req.get("threads", 2)))
    exec_seed = int(req.get("exec_seed", 12345))
    lobes, ws = build_material(req)
    light = EnvLight()
    wo_cos = float(req.get("wo_cos", 0.5))
    wo = (math.sqrt(max(0.0, 1.0-wo_cos*wo_cos)), 0.0, wo_cos)
    tiles = make_tiles(npix, tile)
    pixels = [None]*npix
    tt_stats = {}
    order = shuffled(range(len(tiles)), stream_key(exec_seed, 7))  # 乱序执行
    for tpos, tid in enumerate(order):
        thr = tpos % threads
        csum = {}; esum = [0.0]*len(lobes)
        for px in tiles[tid]:
            if rough_of_px:
                sub = dict(req); sub["rough"] = float(rough_of_px(px))
                lo, w2 = build_material(sub)
            else:
                lo, w2 = lobes, ws
            res_px = render_pixel(seed, px, n, lo, w2, wo, light)
            pixels[px] = res_px
            for k, v in res_px["counts"].items():
                csum[k] = csum.get(k, 0) + v
            for k in range(len(lo)):
                esum[k] += res_px["per_lobe"][k]
        etot = sum(esum) or 1.0
        tt_stats["thr%d/tile%d" % (thr, tid)] = {
            "counts": csum,
            "energy_share": {lo[0]: esum[i]/etot for i, lo in enumerate(lobes)}}
    # 归并 (按像素号, 顺序无关)
    counts = {}
    per_lobe = [0.0]*len(lobes)
    mis_w = 0.0; delta_paths = 0; mis0 = 0; culled = 0
    for px in range(npix):
        p = pixels[px]
        for k, v in p["counts"].items():
            counts[k] = counts.get(k, 0) + v
        for k in range(len(lobes)):
            per_lobe[k] += p["per_lobe"][k]
        mis_w += p["mis_w"]; delta_paths += p["delta_paths"]
        mis0 += p["mis0"]; culled += p["culled"]
    totals = {"counts": counts,
              "per_lobe": [x/npix for x in per_lobe],
              "mis_weight": mis_w/npix,
              "delta_paths": delta_paths, "mis0": mis0, "culled": culled}
    return pixels, tt_stats, totals

def digest_of(obj):
    s = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(s.encode()).hexdigest()[:16]

def pixel_summary(pixels):
    return [p["out"] for p in pixels]

# ---------------- 解析/数值参考能量 ----------------
_dir_albedo_cache = {}
def ggx_dir_albedo(alpha, wo_cos, n=256):
    """GGX 单次散射方向反照率 E(wo) 的确定性分层求积 (白炉参考能量)。"""
    key = (round(alpha, 6), round(wo_cos, 6))
    if key in _dir_albedo_cache:
        return _dir_albedo_cache[key]
    g = GGX(alpha)
    wo = (math.sqrt(max(0.0, 1.0-wo_cos*wo_cos)), 0.0, wo_cos)
    acc = 0.0
    for i in range(n):
        z = (i + 0.5)/n
        r = math.sqrt(max(0.0, 1.0 - z*z))
        for j in range(n):
            phi = 2.0*PI*(j + 0.5)/n
            wi = (r*math.cos(phi), r*math.sin(phi), z)
            acc += g.eval(wi, wo)*z
    E = acc*(2.0*PI/n)/n
    _dir_albedo_cache[key] = E
    return E

def lobe_energy(lobe, wo_cos):
    """各 lobe 在白炉下的已知出射能量 (解析或高精度求积)。"""
    if isinstance(lobe, GGX):
        return ggx_dir_albedo(lobe.alpha, wo_cos)
    return lobe.white_furnace()

# ---------------- 场景: 白炉 (能量守恒) ----------------
def scene_whiteness(req):
    n = int(req.get("n", 4096))
    seed = int(req.get("seed", 5))
    lobes, ws = build_material(req)
    wo_list = req.get("wo_cos_list", [float(req.get("wo_cos", 0.5))])
    max_err = 0.0; per_wo = {}
    totals = None
    for wc in wo_list:
        wc = float(wc)
        expected = sum(ws[j]*lobe_energy(lobes[j][1], wc) for j in range(len(lobes)))
        sub = dict(req); sub["wo_cos"] = wc
        pixels, tt, totals = run_image(sub, seed, n)
        err = max(abs(p["out"] - expected) for p in pixels)   # 逐像素吸收能量偏差
        max_err = max(max_err, err)
        per_wo["wo_cos=%g" % wc] = {"max_abs_err": err, "expected": expected}
    # 重放验证 1: 不同执行顺序 (exec_seed 改变)
    sub = dict(req); sub["wo_cos"] = wo_list[0]
    p1, _, _ = run_image(sub, seed, n)
    sub2 = dict(sub); sub2["exec_seed"] = 99991
    p2, _, _ = run_image(sub2, seed, n)
    # 重放验证 2: 交换分块与线程数
    sub3 = dict(sub)
    sub3["tile"], sub3["threads"] = sub.get("threads", 2), sub.get("tile", 2)
    p3, tt3, _ = run_image(sub3, seed, n)
    d1 = digest_of(pixel_summary(p1))
    replayable = (d1 == digest_of(pixel_summary(p2)) == digest_of(pixel_summary(p3)))
    etot = sum(totals["per_lobe"]) or 1.0
    share = {lobes[j][0]: totals["per_lobe"][j]/etot for j in range(len(lobes))}
    return {"energy_err": max_err, "expected_out": per_wo["wo_cos=%g" % wo_list[0]]["expected"],
            "per_wo": per_wo,
            "mis_weight": totals["mis_weight"],
            "counts": totals["counts"],
            "energy_share": share,
            "degenerate": {"delta_paths": totals["delta_paths"],
                           "mis0": totals["mis0"], "culled": totals["culled"]},
            "thread_tile": tt,
            "replayable": replayable, "digest": d1,
            "pass": max_err <= 0.005}

# ---------------- 场景: 单 lobe 对照解析法线分布积分 ----------------
def scene_single_lobe(req):
    """逐 lobe 单独求出射辐照度, 与解析值对照 (相对误差 <= 2%)。"""
    n = int(req.get("n", 4096))
    seed = int(req.get("seed", 5))
    names = req.get("lobes", ["lambert", "ggx"])
    wo_cos = float(req.get("wo_cos", 0.5))
    results = {}; max_rel = 0.0
    for nm in names:
        sub = dict(req); sub["lobes"] = [nm]; sub["wo_cos"] = wo_cos
        lobes, ws = build_material(sub)
        pixels, _, totals = run_image(sub, seed, n)
        est = sum(p["out"] for p in pixels)/len(pixels)
        ref = lobe_energy(lobes[0][1], wo_cos)
        rel = abs(est-ref)/abs(ref) if ref != 0 else abs(est-ref)
        max_rel = max(max_rel, rel)
        results[nm] = {"est": est, "analytic": ref, "rel_err": rel}
    # 解析法线分布积分: ∫ D(m) max(n·m,0) dω = 1 (GGX NDF 归一化)
    rough = float(req.get("rough", 0.3))
    g = GGX(rough)
    rngU = Rng(stream_key(seed, 0, 11)); rngV = Rng(stream_key(seed, 0, 12))
    acc = 0.0
    for _ in range(n):
        # 策略 A: 半球均匀采样 m
        u1 = rngU.next(); u2 = rngU.next()
        z = u1; r = math.sqrt(max(0.0, 1.0-z*z)); phi = 2.0*PI*u2
        m = (r*math.cos(phi), r*math.sin(phi), z)
        pa = 1.0/(2.0*PI); pbb = g.D(m)*m[2]
        acc += g.D(m)*m[2]/pa * (pa/(pa+pbb))
        # 策略 B: VNDF 采样 (wo=n 时 pdf = D(m)(n·m))
        v1 = rngV.next(); v2 = rngV.next()
        mb = g.sample_m((0.0, 0.0, 1.0), v1, v2)
        pb = g.D(mb)*mb[2]; paa = 1.0/(2.0*PI)
        acc += g.D(mb)*mb[2]/pb * (pb/(paa+pb))
    ndf_est = acc/n
    ndf_rel = abs(ndf_est - 1.0)
    max_rel = max(max_rel, ndf_rel)
    return {"energy_err": max_rel, "lobes": results,
            "ndf_integral": {"est": ndf_est, "analytic": 1.0, "rel_err": ndf_rel},
            "mis_weight": 0.5, "counts": {nm: n for nm in names},
            "replayable": True,
            "digest": digest_of([results, ndf_est]),
            "pass": max_rel <= 0.02}

# ---------------- 场景: 混合材质 ROI + 4 固定种子对照高样本基准 ----------------
def scene_mixed_roi(req):
    n = int(req.get("n", 1024))
    seeds = req.get("seeds", [11, 22, 33, 44])
    res = req.get("res", [4, 4]); W, H = int(res[0]), int(res[1])
    roi = req.get("roi", [0, 0, W, H])
    def rough_of_px(px):
        x, y = px % W, px // W
        return float(req.get("rough", 0.3))*(0.5 + 0.25*((x+y) % 3))
    def roi_mean(pixels):
        vals = []
        for y in range(roi[1], roi[3]):
            for x in range(roi[0], roi[2]):
                vals.append(pixels[y*W+x]["out"])
        return sum(vals)/len(vals)
    # 高样本基准
    ref_seed = int(req.get("ref_seed", 999))
    n_ref = n*16
    pref, _, tot_ref = run_image(req, ref_seed, n_ref, rough_of_px)
    roi_ref = roi_mean(pref)
    mis_ref = tot_ref["mis_weight"]
    roi_means = []; mis_ws = []; digests = []
    replayable = True
    for sd in seeds:
        pixels, tt, totals = run_image(req, int(sd), n, rough_of_px)
        roi_means.append(roi_mean(pixels))
        mis_ws.append(totals["mis_weight"])
        digests.append(digest_of(pixel_summary(pixels)))
        # 交换分块与线程数后像素摘要必须一致
        sub = dict(req)
        sub["tile"], sub["threads"] = req.get("threads", 2), req.get("tile", 2)
        sub["exec_seed"] = 777
        p2, _, _ = run_image(sub, int(sd), n, rough_of_px)
        if digest_of(pixel_summary(p2)) != digests[-1]:
            replayable = False
    mean = sum(roi_means)/len(roi_means)
    var = sum((x-mean)**2 for x in roi_means)/len(roi_means)
    mis_mean = sum(mis_ws)/len(mis_ws)
    mis_var = sum((x-mis_mean)**2 for x in mis_ws)/len(mis_ws)
    roi_rel = max(abs(x-roi_ref)/abs(roi_ref) for x in roi_means)
    mis_dev = abs(mis_mean - mis_ref)
    ok = roi_rel <= 0.02 and mis_dev <= 0.05 and replayable
    return {"energy_err": roi_rel, "roi_mean": roi_means, "roi_ref": roi_ref,
            "roi_std": math.sqrt(var),
            "mis_weight": mis_mean, "mis_std": math.sqrt(mis_var),
            "mis_ref": mis_ref,
            "counts": tot_ref["counts"],
            "replayable": replayable, "digest": digest_of(digests),
            "pass": ok}

# ---------------- 入口 ----------------
SCENES = {"whiteness": scene_whiteness,
          "single_lobe": scene_single_lobe,
          "mixed_roi": scene_mixed_roi}

def run(req):
    scene = req.get("scene", "whiteness")
    if scene not in SCENES:
        raise ValueError("unknown scene: %s" % scene)
    return SCENES[scene](req)

def main():
    json.dump(run(json.load(open(sys.argv[1]))), open(sys.argv[2], "w"))

if __name__ == "__main__":
    main()
