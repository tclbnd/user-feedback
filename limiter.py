#!/usr/bin/env python3
# 砖墙真峰值限制器
#  - 4x 过采样(加窗 sinc 插值)检测采样间真峰值
#  - 预读窗内按起音斜坡预降增益, 保持窗抗锯齿, 释放段按固定速率线性恢复
#  - 两段联动: 段1 包络限制 + 段2 真峰值校验, 所需增益合并后一次包络成形
#  - 分块处理携带包络状态, 块边界增益连续, 结果与块大小无关(确定性)
import json, sys, hashlib, math
from collections import deque

# ---------- 4x 过采样插值器(确定性, 相位0直通, 相位1..3 加窗sinc) ----------
def _build_phase_coefs():
    P, K = 4, 32                     # 4x, 支持域 ±32 个基率样本(逼近理想插值)
    coefs = {}
    for p in range(1, P):
        frac = p / P
        taps = []
        for m in range(-K, K + 1):
            t = frac - m
            s = 1.0 if abs(t) < 1e-15 else math.sin(math.pi * t) / (math.pi * t)
            a = abs(t) / (K + 1)     # Blackman 窗
            w = 0.0 if a >= 1.0 else 0.42 + 0.5 * math.cos(math.pi * a) + 0.08 * math.cos(2.0 * math.pi * a)
            taps.append((m, s * w))
        nrm = sum(w for _, w in taps)   # 归一化直流增益=1
        coefs[p] = tuple((m, w / nrm) for m, w in taps)
    return coefs

_COEFS = _build_phase_coefs()

def oversampled_peak_map(x):
    """每个基率样本的真峰值(4x 过采样 + 抛物线峰值细化, 逼近连续峰)及全局真峰值。"""
    N = len(x)
    M = 4 * N
    K = 32
    xs = [0.0] * M
    xs[0::4] = x                    # 相位0直通
    xl = x
    for p in (1, 2, 3):
        c = _COEFS[p]
        hi = N - K
        for n in range(K, hi):      # 内部: 无需边界钳位
            acc = 0.0
            for m, w in c:
                acc += w * xl[n + m]
            xs[p + 4 * n] = acc
        for n in range(N):          # 边缘: 钳位
            if K <= n < hi:
                continue
            acc = 0.0
            for m, w in c:
                j = n + m
                if j < 0: j = 0
                elif j >= N: j = N - 1
                acc += w * xl[j]
            xs[p + 4 * n] = acc
    ax = [v if v >= 0.0 else -v for v in xs]
    pm = [0.0] * N
    gmax = 0.0
    for n in range(N):              # 各相位采样点峰值
        b = 4 * n
        best = ax[b]
        if ax[b + 1] > best: best = ax[b + 1]
        if ax[b + 2] > best: best = ax[b + 2]
        if ax[b + 3] > best: best = ax[b + 3]
        pm[n] = best
    for i in range(1, M - 1):       # 局部极大处抛物线细化, 归属所在基率样本
        ai = ax[i]
        if ai >= ax[i - 1] and ai >= ax[i + 1]:
            y0, y2 = ax[i - 1], ax[i + 1]
            den = y0 - 2.0 * ai + y2
            if den < 0.0:
                peak = ai - (y0 - y2) * (y0 - y2) / (8.0 * den)
                n = i >> 2
                if peak > pm[n]: pm[n] = peak
    for n in range(N):
        if pm[n] > gmax: gmax = pm[n]
    return pm, gmax

# ---------- 保持窗(最小值滤波, 抗样本级锯齿) ----------
def min_hold(v, H):
    """后向最小值保持: out[n] = min(v[n-H..n]), 单调队列 O(N)。
    桥接周期信号相邻峰间凹陷, 使增益在峰群内不抖动。"""
    N = len(v)
    out = [0.0] * N
    dq = deque()
    for n in range(N):
        while dq and dq[0] < n - H:
            dq.popleft()
        vn = v[n]
        while dq and v[dq[-1]] >= vn:
            dq.pop()
        dq.append(n)
        out[n] = v[dq[0]]
    return out

# ---------- 起音/保持/释放包络整形 ----------
def shape_envelope(r, L, H, step, block):
    """r: 逐样本所需增益(<=1); L: 起音/预读窗(样本); H: 保持窗; step: 每样本释放步进。
    起音: 峰值前 L 样本内线性预降(取所有前瞻约束的最小值);
    保持: 后向最小值窗消除样本级锯齿;
    释放: 每样本最多回升 step(线性, 斜率恰为设定释放速率, 单调非振荡)。
    分块推进并携带包络状态, 块边界连续, 结果与 block 无关。"""
    N = len(r)
    gpre = list(r)
    for k in range(N):
        rk = r[k]
        if rk >= 1.0:
            continue
        c = 1.0 - rk
        lo = k - L
        if lo < 0: lo = 0
        for n in range(lo, k):       # 峰值 k 对 n 的约束: 线性斜坡交汇于 r[k]
            v = rk + c * ((k - n) / L)
            if v < gpre[n]: gpre[n] = v
    if H > 0:
        gpre = min_hold(gpre, H)
    g = [0.0] * N
    prev = 1.0                       # 跨块携带的释放状态
    for b0 in range(0, N, block):
        b1 = b0 + block
        if b1 > N: b1 = N
        for n in range(b0, b1):
            v = gpre[n]
            if v < prev:
                prev = v             # 强制下降(起音/保持约束)
            elif v > prev + step:
                prev += step         # 释放: 固定步进, 斜率=设定速率
            # 否则保持平台(上升滞回, 消除保持窗微步进抖动)
            if prev > 1.0: prev = 1.0
            g[n] = prev
    return g

# ---------- 主限制流程(两段联动) ----------
def limit(x, sr, ceil_db, attack_ms, release_ms, block):
    N = len(x)
    ceil = 10.0 ** (ceil_db / 20.0)
    tgt = ceil * (10.0 ** (-0.05 / 20.0))   # 0.05 dB 余量, 保证真峰值严格 <= ceiling
    if N == 0:
        return [], [], {"max_peak": 0.0, "true_peak": 0.0, "true_peak_db": -300.0,
                        "limited": 0, "min_gain": 1.0, "max_gain_reduction_db": 0.0}
    L = max(1, int(round(attack_ms * sr / 1000.0)))
    H = int(round(0.005 * sr))              # 5 ms 保持窗
    R = max(1, int(round(release_ms * sr / 1000.0)))
    step = 1.0 / R                          # 释放速率: 满量程恢复需 release_ms

    # 段1: 过采样真峰值检测 -> 所需增益
    pm, _ = oversampled_peak_map(x)
    r = [1.0 if p <= tgt else tgt / p for p in pm]
    g = shape_envelope(r, L, H, step, block)

    # 段2: 对输出重新做 4x 真峰值校验, 残余超限所需增益并入 r 后重新成形(联动)
    converged = False
    for _ in range(6):
        y = [x[n] * g[n] for n in range(N)]
        py, pmax = oversampled_peak_map(y)
        if pmax <= tgt:
            converged = True
            break
        for n in range(N):
            if py[n] > tgt:
                rn = r[n] * (tgt / py[n])
                r[n] = rn if rn < 1.0 else 1.0
        g = shape_envelope(r, L, H, step, block)

    # 最终逐点保证(正常路径不会触发, 仅作硬保证, 迭代收敛)
    if not converged:
        for _ in range(50):
            for n in range(N):
                if py[n] > tgt:
                    g[n] *= tgt / py[n]
            y = [x[n] * g[n] for n in range(N)]
            py, pmax = oversampled_peak_map(y)
            if pmax <= tgt:
                break

    out = y
    tp = pmax
    mp = max(abs(v) for v in out)
    gmin = min(g)
    stats = {
        "max_peak": mp,
        "true_peak": tp,
        "true_peak_db": 20.0 * math.log10(tp) if tp > 0.0 else -300.0,
        "limited": sum(1 for v in g if v < 1.0 - 1e-9),
        "min_gain": gmin,
        "max_gain_reduction_db": -20.0 * math.log10(gmin) if gmin < 1.0 else 0.0,
    }
    return out, g, stats

def main():
    req = json.load(open(sys.argv[1]))
    sr = req.get("sr", 48000)
    block = max(1, int(req.get("block", 1024)))
    # seed: 处理完全确定性, 不依赖随机数; 接受该参数仅为兼容
    _seed = req.get("seed", 0)
    out, gain, stats = limit(req["samples"], sr,
                             req.get("ceiling_db", -0.1),
                             req.get("attack_ms", 1.0),
                             req.get("release_ms", 50.0),
                             block)
    res = {"out": out, "gain": gain, "stats": stats,
           "digest": hashlib.sha1(",".join(f"{v:.4f}" for v in out).encode()).hexdigest()}
    json.dump(res, open(sys.argv[2], "w"))

if __name__ == "__main__":
    main()
