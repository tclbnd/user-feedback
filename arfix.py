"""损坏样本区间的爆点检测与自回归(AR)双向插值修复.

用法: python3 arfix.py input.json output.json
input.json:  {"sr":48000,"samples":[float],"block":512,"seed":1}
output.json: {"out":[...],"repairs":[[start,len]...],"stats":{...},"digest":"sha1"}

方法:
1) 检测: 相邻样本差分 d[k]=x[k+1]-x[k], 中位数/MAD 稳健估计本底,
   |d-med|>6*sigma 的边标记为爆点边; 间隔<=3 的近邻边归并为一个
   损坏区间, 区间样本为 e0+1..e1 (孤立尖刺与宽脉冲均精确定位).
   长度>=2 的精确置零段直接判为长损坏; 与零段相邻的边不重复归因.
   区间长度<=3 判为孤立爆点, 否则为长损坏.
2) 修复: 取损坏段两侧干净上下文(不跨越其它损坏), Levinson-Durbin
   拟合 AR 系数, 自左边界前向预测、自右边界反向预测, 余弦交叉
   淡化混合, 两端与干净波形自然衔接, 边界相位连续.
3) 分段处理: 检测统计量全局稳健估计, 修复仅依赖区间两侧上下文,
   按 block 分块调度但结果与块大小无关 -> 块边界一致, 复跑摘要一致.
"""
import json, sys, hashlib, math


def _median(v):
    s = sorted(v)
    n = len(s)
    if n == 0:
        return 0.0
    m = n // 2
    return s[m] if n % 2 else 0.5 * (s[m - 1] + s[m])


def _detect(x):
    """返回布尔掩码: True=损坏样本."""
    n = len(x)
    mask = [False] * n
    # 1) 精确置零的长损坏段
    i = 0
    while i < n:
        if x[i] == 0.0:
            j = i
            while j < n and x[j] == 0.0:
                j += 1
            if j - i >= 2:
                for k in range(i, j):
                    mask[k] = True
            i = j
        else:
            i += 1
    # 2) 相邻样本差分能量检测爆点边
    if n >= 8:
        d = [x[k + 1] - x[k] for k in range(n - 1)]
        med = _median(d)
        mad = _median([abs(v - med) for v in d])
        sig = 1.4826 * mad
        thr = max(6.0 * sig, 1e-9)
        edges = [k for k in range(n - 1)
                 if abs(d[k] - med) > thr and x[k] != 0.0 and x[k + 1] != 0.0]
        # 间隔<=3 的近邻边归并为一组 -> 损坏样本 e0+1..e1
        groups = []
        for k in edges:
            if groups and k - groups[-1][-1] <= 3:
                groups[-1].append(k)
            else:
                groups.append([k])
        for g in groups:
            for t in range(g[0] + 1, min(g[-1] + 1, n)):
                mask[t] = True
    return mask


def _intervals(mask):
    iv = []
    i = 0
    n = len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            iv.append((i, j))
            i = j
        else:
            i += 1
    return iv


def _burg(segs, p):
    """Burg 法在多段上下文上估计 AR 预测系数 c[1..p]:
    x[t] ~= sum c[i] x[t-i]. 短记录下比 Yule-Walker 频率估计准, 且保证稳定.
    格型递归: f_m[t]=f_{m-1}[t]-k*b_{m-1}[t-1]; b_m[t]=b_{m-1}[t-1]-k*f_{m-1}[t].
    长度 <= m 的段在对应阶自然停止贡献, 故 p 只受最长段约束."""
    p = max(1, min(p, max(len(s) for s in segs) - 1))
    fs = [list(s) for s in segs]
    bs = [list(s) for s in segs]
    c = [0.0] * (p + 1)
    for m in range(1, p + 1):
        num = 0.0
        den = 0.0
        for f, b in zip(fs, bs):
            n = len(f)
            for t in range(m, n):
                num += f[t] * b[t - 1]
                den += f[t] * f[t] + b[t - 1] * b[t - 1]
        k = 2.0 * num / den if den > 1e-30 else 0.0
        prev = c[:]
        c[m] = k
        for i in range(1, m):
            c[i] = prev[i] - k * prev[m - i]
        for j in range(len(fs)):
            f = fs[j]
            b = bs[j]
            n = len(f)
            f_old = f[:]
            b_old = b[:]
            for t in range(m, n):
                f[t] = f_old[t] - k * b_old[t - 1]
                b[t] = b_old[t - 1] - k * f_old[t]
    # 最终阶前向新息 RMS 作为拟合统计
    acc = 0.0
    cnt = 0
    for f in fs:
        for v in f[p:]:
            acc += v * v
            cnt += 1
    rms = math.sqrt(acc / cnt) if cnt else 0.0
    return c, rms


def _interp(left, right, m, c, p):
    """用 AR 系数 c 在缺口两侧分别前向/反向预测并余弦加权混合.
    权重按两侧上下文长度调节; 边界处权重趋于本侧 1, 保证相位连续."""
    fwd = bwd = None
    if left:
        if len(left) < p:
            hist = [left[0]] * (p - len(left)) + list(left)
        else:
            hist = list(left[-p:])
        fwd = []
        for _ in range(m):
            v = 0.0
            for i in range(1, p + 1):
                v += c[i] * hist[-i]
            fwd.append(v)
            hist.append(v)
            hist = hist[-p:]
    if right:
        fut = list(right[:p])
        if len(fut) < p:
            fut = fut + [fut[-1]] * (p - len(fut))
        bwd = [0.0] * m
        for k in range(m - 1, -1, -1):
            v = 0.0
            for i in range(1, p + 1):
                v += c[i] * fut[i - 1]
            bwd[k] = v
            fut = [v] + fut[:p - 1]
    if fwd is None and bwd is None:
        return [0.0] * m
    if fwd is None:
        return bwd
    if bwd is None:
        return fwd
    sl = max(float(len(left)), 1.0)
    sb = max(float(len(right)), 1.0)
    seg = []
    for k in range(m):
        w0 = 0.5 * (1.0 + math.cos(math.pi * (k + 1) / (m + 1)))
        wf = w0 * sl
        wb = (1.0 - w0) * sb
        w = wf / (wf + wb)
        seg.append(w * fwd[k] + (1.0 - w) * bwd[k])
    return seg


def _repair(x, mask, s, e, gsegs=()):
    """修复区间 [s,e), 返回 (新样本列表, 统计信息).
    gsegs: 全信号干净段列表 [(a,b)], 局部上下文不足时联合参与 AR 估计."""
    n = len(x)
    m = e - s
    # 两侧干净上下文(不跨越其它损坏)
    L = max(1024, 3 * m, 96)
    l0 = s
    while l0 > 0 and s - l0 < L and not mask[l0 - 1]:
        l0 -= 1
    left = x[l0:s]
    r1 = e
    while r1 < n and r1 - e < L and not mask[r1]:
        r1 += 1
    right = x[e:r1]
    segs = [sg for sg in (left, right) if len(sg) >= 8]
    # 两侧上下文都不足(损坏密集或靠近信号端)时, 联合全信号干净段估计 AR
    if gsegs and (not segs or max(len(left), len(right)) < 256):
        for a, b in gsegs:
            if b == s or a == e:
                continue  # 左右邻段已在上下文中
            if b - a >= 48:
                segs.append(x[a:min(b, a + 384)])
    p = 0
    innov = 0.0
    if segs:
        p = max(4, min(40, (max(len(sg) for sg in segs) - 1) // 3))
        c, innov = _burg(segs, p)
        seg = _interp(left, right, m, c, p)
    else:
        seg = [0.0] * m
    info = {"start": s, "len": m,
            "type": "click" if m <= 3 else "long",
            "ar_order": p,
            "innov": round(math.sqrt(max(innov, 0.0)), 6),
            "ctx": [len(left), len(right)]}
    return seg, info


def process(samples, block=512):
    """分块调度处理; 检测与修复均不依赖块边界, 结果与 block 无关."""
    x = list(samples)
    n = len(x)
    mask = _detect(x)
    iv = _intervals(mask)
    # 全信号干净段(损坏密集时联合估计 AR 用)
    gsegs = []
    i = 0
    while i < n:
        if not mask[i]:
            j = i
            while j < n and not mask[j]:
                j += 1
            if j - i >= 48:
                gsegs.append((i, j))
            i = j
        else:
            i += 1
    out = list(x)
    repairs = []
    events = []
    done = [False] * len(iv)
    block = max(1, int(block))
    for b0 in range(0, max(n, 1), block):
        b1 = min(n, b0 + block)
        for idx, (s, e) in enumerate(iv):
            if done[idx] or not (b0 <= s < b1):
                continue
            done[idx] = True
            seg, info = _repair(x, mask, s, e, gsegs)
            out[s:e] = seg
            repairs.append([s, e - s])
            events.append(info)
    # 第二遍精修: 在修复后的完整信号上重新取双侧上下文拟合 AR 并重插值,
    # 上下文不再被损坏截断, 频率/相位估计更准(对长损坏段提升明显)
    for s, e in iv:
        m = e - s
        l0 = max(0, s - 1024)
        r1 = min(n, e + 1024)
        left = out[l0:s]
        right = out[e:r1]
        segs = [sg for sg in (left, right) if len(sg) >= 8]
        if not segs:
            continue
        p = max(4, min(40, (max(len(sg) for sg in segs) - 1) // 3))
        c, _ = _burg(segs, p)
        out[s:e] = _interp(left, right, m, c, p)
    repairs.sort()
    events.sort(key=lambda d: d["start"])
    stats = {
        "events": events,
        "n_click": sum(1 for d in events if d["type"] == "click"),
        "n_long": sum(1 for d in events if d["type"] == "long"),
        "total_repaired": sum(d["len"] for d in events),
    }
    digest = hashlib.sha1(
        ",".join(f"{v:.4f}" for v in out[:64]).encode()).hexdigest()
    return out, repairs, stats, digest


def main():
    req = json.load(open(sys.argv[1]))
    out, repairs, stats, digest = process(req["samples"], req.get("block", 512))
    json.dump({"out": out, "repairs": repairs, "stats": stats,
               "digest": digest}, open(sys.argv[2], "w"))


if __name__ == "__main__":
    main()
