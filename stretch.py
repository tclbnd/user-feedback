"""双通道变速重放: 速度积分映射 + 共享瞬态处理的 WSOLA 时域拉伸。

用法: python3 stretch.py input.json output.json
输入: rate(采样率), blocks(交错双通道帧块), knots([[输出采样索引, 速度], ...])
输出: frames(双通道帧), mapping(每输出采样的理论输入坐标),
      transients(输入/输出瞬态索引), latency(处理等待帧数, 输出坐标已补偿)

算法要点
--------
1. 速度曲线定义在输出采样轴上(结点间线性, 末端保持), 输入坐标 x(t) 为其
   解析积分; 输出长度为最小整数 t 使 x(t) >= 源帧数。mapping 逐样本给出
   理论 x(t), 与分块方式无关。
2. 时域 WSOLA: 合成帧按固定输出时钟(Hs)推进, 每帧按 x(t) 取得目标输入
   位置, 在邻域内做波形相关搜索保持持续区相位连续(音高自然保持)。
   两个声道共用同一组读写位置, 相对增益/极性关系得以保留。
3. 瞬态(能量通量检测, 作用于双声道混合)采用固定策略: 最近帧把窗口中心
   对齐到瞬态(相位重置, 不再做相关搜索), 其前/后帧的窗口被约束在瞬态
   之外, 避免同一鼓点被重叠成两次起音。
4. 全部输入块先拼接为连续流再处理, 块界不影响任何连续时间状态;
   短文件与全静音输入均可有限终止。
"""

import argparse
import bisect
import json
import math
from array import array
from pathlib import Path

try:  # C 加速的相关搜索; 不可用时退化为纯 Python 两级搜索
    import audioop
except ImportError:  # pragma: no cover
    audioop = None


# ---------------------------------------------------------------- 速度曲线

class SpeedCurve:
    """定义在输出采样轴上的分段线性速度曲线, 末端保持末值。"""

    def __init__(self, knots):
        pts = sorted((int(i), float(s)) for i, s in knots)
        if not pts or pts[0][0] != 0:
            raise ValueError("knots must start at index 0")
        self.idx = [p[0] for p in pts]
        self.spd = [p[1] for p in pts]
        self.cum = [0.0]  # 各结点处的积分值 x(idx)
        for (i0, s0), (i1, s1) in zip(pts, pts[1:]):
            self.cum.append(self.cum[-1] + (i1 - i0) * 0.5 * (s0 + s1))

    def x(self, t):
        """输出坐标 t(实数, 单位:采样)对应的输入坐标。"""
        idx, spd, cum = self.idx, self.spd, self.cum
        if t <= 0:
            return 0.0
        if len(idx) == 1 or t >= idx[-1]:
            return cum[-1] + spd[-1] * (t - idx[-1])
        i = bisect.bisect_right(idx, t) - 1
        i0, i1 = idx[i], idx[i + 1]
        s0, s1 = spd[i], spd[i + 1]
        tau = t - i0
        return cum[i] + s0 * tau + 0.5 * (s1 - s0) / (i1 - i0) * tau * tau

    def invert(self, y):
        """输入坐标 y 对应的输出坐标(实数)。"""
        idx, spd, cum = self.idx, self.spd, self.cum
        if y <= 0:
            return 0.0
        if len(idx) == 1 or y >= cum[-1]:
            return idx[-1] + (y - cum[-1]) / spd[-1]
        i = bisect.bisect_right(cum, y) - 1
        i = min(i, len(idx) - 2)
        i0, i1 = idx[i], idx[i + 1]
        s0, s1 = spd[i], spd[i + 1]
        dy = y - cum[i]
        slope = (s1 - s0) / (i1 - i0)
        if abs(slope) < 1e-15:
            return i0 + dy / s0
        disc = s0 * s0 + 2.0 * slope * dy
        return i0 + (-s0 + math.sqrt(max(disc, 0.0))) / slope

    def output_length(self, n_in):
        """最小整数 t 使 x(t) >= n_in。"""
        if n_in <= 0:
            return 0
        t = max(0, int(math.floor(self.invert(n_in))) - 1)
        while t > 0 and self.x(t) >= n_in:
            t -= 1
        while self.x(t) < n_in:
            t += 1
        return t

    def mapping(self, n_out):
        """逐输出样本的理论输入坐标。"""
        idx, spd, cum = self.idx, self.spd, self.cum
        out = [0.0] * n_out
        seg = 0
        nseg = len(idx) - 1
        for t in range(n_out):
            while seg < nseg - 1 and t >= idx[seg + 1]:
                seg += 1
            if nseg == 0 or t >= idx[-1]:
                out[t] = cum[-1] + spd[-1] * (t - idx[-1])
            else:
                i0, i1 = idx[seg], idx[seg + 1]
                s0, s1 = spd[seg], spd[seg + 1]
                tau = t - i0
                out[t] = cum[seg] + s0 * tau + 0.5 * (s1 - s0) / (i1 - i0) * tau * tau
        return out


# ---------------------------------------------------------------- 瞬态检测

def detect_transients(mono, fh, med_half, min_gap):
    """混合信号上的能量通量瞬态检测, 返回输入采样索引列表(升序)。"""
    n = len(mono)
    nf = n // fh
    if nf < 4:
        return []
    # 高通(一阶差分)能量的滑动窗和
    c = [0.0] * (n + 1)
    run = 0.0
    prev = mono[0]
    for i in range(1, n):
        d = mono[i] - prev
        prev = mono[i]
        run += d * d
        c[i + 1] = run
    eps = 1e-12
    loge = [math.log((c[min((i + 1) * fh, n)] - c[i * fh]) + eps) for i in range(nf)]
    flux = [0.0] * nf
    for i in range(1, nf):
        d = loge[i] - loge[i - 1]
        if d > 0.0:
            flux[i] = d
    fmax = max(flux)
    if fmax <= 1e-9:
        return []
    floor = 0.05 * fmax
    cands = []
    for i in range(1, nf - 1):
        f = flux[i]
        if f < floor or f < flux[i - 1] or f < flux[i + 1]:
            continue
        lo = max(0, i - med_half)
        hi = min(nf, i + med_half + 1)
        seg = sorted(flux[lo:hi])
        med = seg[(hi - lo) // 2]
        if f > 1.7 * med + 1e-9:
            cands.append(i)
    # 最小间隔合并, 保留最强者
    picked = []
    for i in cands:
        pos = i * fh
        if picked and pos - picked[-1][0] < min_gap:
            if flux[i] > picked[-1][1]:
                picked[-1] = (pos, flux[i])
        else:
            picked.append((pos, flux[i]))
    return [p for p, _ in picked]


# ---------------------------------------------------------------- 相关搜索

def _to_pcm16(vals, scale=8192.0):
    out = array("h")
    ap = out.append
    for v in vals:
        if v > 3.9:
            v = 3.9
        elif v < -3.9:
            v = -3.9
        ap(int(v * scale))
    return out.tobytes()


def wsola_offset(mono, lo, hi, ref, olen):
    """在 mono 的 [lo, hi] 起点范围内找与 ref 最匹配的窗口起点偏移。

    返回相对 lo 的整数偏移; ref 能量过低(静音)时返回 None 表示无需搜索。
    """
    width = hi - lo
    if width <= 0:
        return 0
    rmax = 0.0
    for v in ref:
        av = -v if v < 0 else v
        if av > rmax:
            rmax = av
    if rmax < 1e-6:  # 静音区: 保持理论位置
        return None
    if audioop is not None:
        frag = _to_pcm16(mono[lo:hi + olen])
        refb = _to_pcm16(ref)
        try:
            off, fac = audioop.findfit(frag, refb)
        except Exception:
            off, fac = -1, float("nan")
        if off >= 0 and fac == fac:  # 排除 nan
            return off
        return None
    # 纯 Python 退化路径: 8 倍抽取粗搜 + 精修
    step = 8
    dec_ref = ref[::step]
    best_o, best_e = 0, None
    for o in range(0, width + 1, step):
        seg = mono[lo + o:lo + o + olen:step]
        e = 0.0
        for a, b in zip(seg, dec_ref):
            d = a - b
            e += d * d
        if best_e is None or e < best_e:
            best_e, best_o = e, o
    best_o2, best_e2 = best_o, None
    for o in range(max(0, best_o - step), min(width, best_o + step) + 1):
        seg = mono[lo + o:lo + o + olen]
        e = 0.0
        for a, b in zip(seg, ref):
            d = a - b
            e += d * d
        if best_e2 is None or e < best_e2:
            best_e2, best_o2 = e, o
    return best_o2


# ---------------------------------------------------------------- 主拉伸器

def stretch_frames(frames, rate, knots):
    """frames: [[L, R], ...]; 返回 (out_frames, mapping, transients, latency)。"""
    n_in = len(frames)
    curve = SpeedCurve(knots)
    n_out = curve.output_length(n_in)
    if n_in == 0 or n_out == 0:
        return [], curve.mapping(n_out), {"input": [], "output": []}, 0

    # 以 32ms 窗为基准的参数(随采样率缩放)
    W = 1 << int(round(math.log(max(rate * 0.032, 128.0), 2)))
    W = max(128, min(4096, W))
    W2 = W // 2
    Hs = W // 8
    S = int(round(0.010 * rate))          # WSOLA 搜索半径
    guard = int(round(0.003 * rate))      # 瞬态保护间隔
    fh = Hs                               # 通量分析步长
    med_half = max(1, int(round(0.05 * rate / fh)))
    min_gap = max(int(round(0.05 * rate)), W + 2 * guard)
    latency = W + S + (med_half + 2) * fh  # 输出坐标已补偿, 仅作导出

    L = [float(f[0]) for f in frames]
    R = [float(f[1]) for f in frames]
    mono = [(l + r) * 0.5 for l, r in zip(L, R)]

    transients = detect_transients(mono, fh, med_half, min_gap)

    # 右侧补零, 覆盖文件尾部的窗口越界读取; 左侧补零覆盖首帧的负坐标
    # (合成帧中心从输出坐标 0 开始, 首帧窗口左缘为负, 读写位置受控)。
    PL = W2 + S + 8
    pad_end = 2 * W + S + 16
    Lp = [0.0] * PL + L + [0.0] * pad_end
    Rp = [0.0] * PL + R + [0.0] * pad_end
    max_start = n_in + pad_end - W
    monop = [0.0] * PL + mono + [0.0] * pad_end

    win = [0.5 - 0.5 * math.cos(2.0 * math.pi * (i + 0.5) / W) for i in range(W)]
    # 合成帧按固定输出时钟推进, 帧中心 oc = k*Hs + W2 覆盖 [0, n_out)。
    k_min = -(W2 // Hs)
    k_end = (n_out - W2 + Hs - 1) // Hs   # 首个使 oc >= n_out 的 k(不含)
    OFF = W                               # 输出缓冲偏移, 容纳负 o
    total = n_out + 2 * W + Hs
    outL = [0.0] * total
    outR = [0.0] * total
    wsum = [0.0] * total
    OL = W - Hs                           # 重叠区长度(相关搜索参考)

    consumed = set()
    snapped_out = {}
    ntr = len(transients)

    for k in range(k_min, k_end):
        o = k * Hs
        oc = o + W2
        pc = curve.x(oc)                  # 目标输入中心(逐样本一致的速度积分)
        ow = o + OFF                      # 输出缓冲写位置

        # 1) 瞬态吸附: 最近的未消费瞬态落在吸附区内 -> 窗口中心对齐瞬态(相位重置)
        j = bisect.bisect_left(transients, pc - W2)
        tstar = None
        best_d = None
        while j < ntr and transients[j] <= pc + W2:
            t = transients[j]
            if t not in consumed:
                d = abs(t - pc)
                if best_d is None or d < best_d:
                    best_d, tstar = d, t
            j += 1
        if tstar is not None:
            p_start = tstar - W2
            consumed.add(tstar)
            snapped_out[tstar] = oc
        else:
            # 2) 窗重叠约束: 窗口不得覆盖任何瞬态(防止一次鼓点出现两次起音)
            lo = pc - W2 - S
            hi = pc + W2 + S
            j = bisect.bisect_left(transients, lo - guard)
            while j < ntr and transients[j] <= hi + W + guard:
                t = transients[j]
                if t > pc:
                    if t - guard - W < hi:
                        hi = t - guard - W
                elif t < pc:
                    if t + guard > lo:
                        lo = t + guard
                j += 1
            if lo < -PL:
                lo = float(-PL)
            if hi < -PL:
                hi = float(-PL)
            if lo > hi:                   # 约束冲突(瞬态过密): 固定为理论位置
                lo = hi = pc - W2
            ilo = int(math.ceil(lo))
            ihi = int(math.floor(hi))
            ref = [outL[ow + i] + outR[ow + i] for i in range(OL)]
            off = wsola_offset(monop, ilo + PL, ihi + PL, ref, OL)
            if off is None:
                p_start = min(max(int(round(pc - W2)), ilo), ihi)
            else:
                p_start = ilo + off
        if p_start < -PL:
            p_start = -PL
        elif p_start > max_start:
            p_start = max_start

        # 3) 两个声道共用 p_start 做重叠相加
        ps = p_start + PL
        segL = Lp[ps:ps + W]
        segR = Rp[ps:ps + W]
        outL[ow:ow + W] = [a + w * b for a, w, b in zip(outL[ow:ow + W], win, segL)]
        outR[ow:ow + W] = [a + w * b for a, w, b in zip(outR[ow:ow + W], win, segR)]
        wsum[ow:ow + W] = [a + w for a, w in zip(wsum[ow:ow + W], win)]

    for i in range(n_out):
        w = wsum[i + OFF]
        if w > 1e-9:
            outL[i + OFF] /= w
            outR[i + OFF] /= w

    out_frames = [[outL[i + OFF], outR[i + OFF]] for i in range(n_out)]
    tr_out = [snapped_out.get(t, int(round(curve.invert(t)))) for t in transients]
    return out_frames, curve.mapping(n_out), {"input": transients, "output": tr_out}, latency


# ---------------------------------------------------------------- JSON 接口

def render(data):
    frames = [frame for block in data["blocks"] for frame in block]
    out_frames, mapping, transients, latency = stretch_frames(
        frames, int(data["rate"]), data["knots"])
    return {
        "frames": out_frames,
        "mapping": mapping,
        "transients": transients,
        "latency": latency,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("input", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    result = render(json.loads(a.input.read_text()))
    a.output.write_text(json.dumps(result, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
