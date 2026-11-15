"""谱通量起始点检测 + 自适应中位数阈值 + 最小间隔合并 + 节拍栅格估计与吸附。

用法: python3 onset.py input.json output.json
input.json: {"sr":16000,"frame":512,"hop":128,"audio":[float...],
             "segments":[[s0,s1],...]  # 可选, 帧起点区间(采样点), 乱序分段处理
            }
output.json: {"onsets_ms":[...],"confidence":[...],"bpm":..,
              "grid":{"period_ms":..,"phase_ms":..,"coverage":..,"snapped_ms":[...]},
              "digest":"sha1(起始点毫秒整数)"}
纯标准库实现, 全程确定性; 帧长/帧移变化下判定一致。
"""
import json, sys, math, cmath, hashlib, statistics

# ---------------- FFT (纯 Python, radix-2) ----------------

def _fft(a):
    n = len(a)
    if n == 1:
        return a
    even = _fft(a[0::2])
    odd = _fft(a[1::2])
    out = [0j] * n
    w = 1 + 0j
    step = cmath.exp(-2j * math.pi / n)
    h = n // 2
    for k in range(h):
        t = w * odd[k]
        out[k] = even[k] + t
        out[k + h] = even[k] - t
        w *= step
    return out

# ---------------- 分帧 + 谱通量 ----------------

def frame_mags(audio, frame, hop, sr=16000, segments=None):
    """返回 [(frame_start_sample, [|X(k)|, k=0..nfft/2])]。
    segments: 可选, [(s0,s1)] 帧起点区间(含端点), 可乱序/重叠; 结果按起点去重排序,
              与整段一次性处理完全一致(帧由其起点唯一确定)。"""
    n = len(audio)
    last = n - frame
    if last < 0:
        return []
    win = [0.5 - 0.5 * math.cos(2.0 * math.pi * i / (frame - 1)) for i in range(frame)]
    nfft = 1
    while nfft < frame:
        nfft <<= 1
    nb = nfft // 2 + 1
    segs = segments if segments else [(0, last)]
    # 频率域平滑半宽: 约 +-45Hz 的带内平均, 使颤音的亚带频移在带内抵消,
    # 且平滑宽度按 bin 带宽折算, 帧长变化下行为一致
    bin_hz = sr / nfft
    fh = max(1, int(round(45.0 / bin_hz)))
    seen = {}
    for (s0, s1) in segs:
        s1 = min(s1, last)
        i = s0
        while i <= s1:
            if i not in seen:
                seg = [audio[i + j] * win[j] for j in range(frame)]
                if nfft > frame:
                    seg = seg + [0.0] * (nfft - frame)
                spec = _fft([complex(v, 0.0) for v in seg])
                mag = [abs(spec[k]) / nfft for k in range(nb)]
                sm = []
                for k in range(nb):
                    a = max(0, k - fh)
                    b = min(nb, k + fh + 1)
                    sm.append(sum(mag[a:b]) / (b - a))
                seen[i] = sm
            i += hop
    return sorted(seen.items())

def spectral_flux(mags):
    """逐帧幅度正差分求和(半谱), 首帧为 0。"""
    flux = [0.0]
    for (_, m0), (_, m1) in zip(mags, mags[1:]):
        s = 0.0
        for a, b in zip(m0, m1):
            d = b - a
            if d > 0.0:
                s += d
        flux.append(s / len(m1))
    return flux

def smooth_flux(flux):
    """3 帧 [0.25,0.5,0.25] 平滑: 抑制衰减音泄漏拍频造成的单帧毛刺,
    保留 2-4 帧宽的真实起音峰。"""
    n = len(flux)
    out = []
    for i in range(n):
        a = flux[i - 1] if i > 0 else flux[i]
        b = flux[i]
        c = flux[i + 1] if i < n - 1 else flux[i]
        out.append(0.25 * a + 0.5 * b + 0.25 * c)
    return out

# ---------------- 自适应阈值 + 峰值拾取 ----------------

def adaptive_threshold(flux, sr, hop, k=1.8, c_mad=4.5, med_win_ms=400.0,
                       delta_ratio=0.05):
    """阈值 = max(k*局部中位数, 局部中位数 + c*局部MAD) + delta。
    中位数与 MAD 均为稳健统计量: 稀疏起音峰不占窗口多数, 不影响底噪估计;
    颤音段通量持续振荡, MAD 增大自动抬高阈值; 安静段 MAD 极小,
    阈值贴近底噪, 弱起音可过阈。"""
    w = max(1, int(round(med_win_ms * sr / 1000.0 / hop)))
    gmed = statistics.median(flux) if flux else 0.0
    delta = delta_ratio * gmed + 1e-12
    n = len(flux)
    thr = []
    for i in range(n):
        lo = max(0, i - w)
        hi = min(n, i + w + 1)
        win = flux[lo:hi]
        med = statistics.median(win)
        mad = statistics.median([abs(x - med) for x in win])
        thr.append(max(k * med, med + c_mad * mad) + delta)
    return thr

def pick_peaks(flux, thr, starts, sr, hop, frame, nb=2):
    """局部极大且过阈; 抛物线插值获得亚帧时间。
    起音能量集中在包含它的帧中心附近, 故时间取帧中心, 消除帧长相关偏差,
    保证帧长/帧移变化下判定一致。"""
    n = len(flux)
    peaks = []
    for i in range(n):
        f = flux[i]
        if f <= 0.0 or f <= thr[i]:
            continue
        lo = max(0, i - nb)
        hi = min(n, i + nb + 1)
        if any(flux[j] > f for j in range(lo, hi)):
            continue
        off = 0.0
        if 0 < i < n - 1:
            y0, y1, y2 = flux[i - 1], flux[i], flux[i + 1]
            den = y0 - 2.0 * y1 + y2
            if den < 0.0:
                off = 0.5 * (y0 - y2) / den
                off = max(-1.0, min(1.0, off))
        t_ms = (starts[i] + frame / 2.0 + off * hop) / sr * 1000.0
        conf = (f - thr[i]) / (f + thr[i])
        peaks.append((t_ms, conf))
    return peaks

def merge_min_interval(onsets, min_interval_ms=50.0):
    """小于最小间隔的起始点合并, 保留置信度高者(及其时间)。"""
    merged = []
    for t, c in sorted(onsets):
        if merged and t - merged[-1][0] < min_interval_ms:
            if c > merged[-1][1]:
                merged[-1] = (t, c)
        else:
            merged.append((t, c))
    return merged

# ---------------- 节拍栅格 ----------------

def estimate_grid(onsets, bin_ms=10.0, ioi_lo=150.0, ioi_hi=2000.0):
    """间隔直方图估计栅格周期, 圆均值估计相位, 输出吸附结果与覆盖率。"""
    ts = [t for t, _ in onsets]
    cs = [c for _, c in onsets]
    if len(ts) < 3:
        return None
    hist = {}
    for i in range(len(ts) - 1):
        ioi = ts[i + 1] - ts[i]
        if ioi_lo <= ioi <= ioi_hi:
            b = int(ioi / bin_ms)
            w = math.sqrt(cs[i] * cs[i + 1]) + 1e-6
            hist[b] = hist.get(b, 0.0) + w
    if not hist:
        return None
    peak = max(hist, key=hist.get)
    vals, wts = [], []
    for i in range(len(ts) - 1):
        ioi = ts[i + 1] - ts[i]
        if abs(ioi / bin_ms - peak) <= 1.0:
            w = math.sqrt(cs[i] * cs[i + 1]) + 1e-6
            vals.append(ioi)
            wts.append(w)
    period = sum(v * w for v, w in zip(vals, wts)) / sum(wts)
    # 相位: 单位圆加权均值
    sx = sy = 0.0
    for t, c in zip(ts, cs):
        ang = 2.0 * math.pi * (t % period) / period
        sx += c * math.cos(ang)
        sy += c * math.sin(ang)
    phase = (math.atan2(sy, sx) / (2.0 * math.pi)) * period % period
    tol = max(15.0, 0.08 * period)
    snapped, n_cov = [], 0
    for t in ts:
        g = phase + round((t - phase) / period) * period
        if abs(g - t) <= tol:
            n_cov += 1
        snapped.append(g)
    return {
        "period_ms": period,
        "phase_ms": phase,
        "bpm": 60000.0 / period,
        "coverage": n_cov / len(ts),
        "snapped_ms": snapped,
    }

# ---------------- 顶层检测 ----------------

def detect(audio, sr=16000, frame=512, hop=128, segments=None,
           k=1.8, med_win_ms=400.0, min_interval_ms=50.0):
    mags = frame_mags(audio, frame, hop, sr, segments)
    starts = [s for s, _ in mags]
    flux = smooth_flux(spectral_flux(mags))
    thr = adaptive_threshold(flux, sr, hop, k=k, med_win_ms=med_win_ms)
    peaks = pick_peaks(flux, thr, starts, sr, hop, frame)
    onsets = merge_min_interval(peaks, min_interval_ms)
    grid = estimate_grid(onsets)
    ts = [t for t, _ in onsets]
    return {
        "onsets_ms": ts,
        "confidence": [c for _, c in onsets],
        "bpm": grid["bpm"] if grid else None,
        "grid": grid,
        "digest": hashlib.sha1(",".join(f"{t:.0f}" for t in ts).encode()).hexdigest(),
    }

def main():
    req = json.load(open(sys.argv[1]))
    segs = req.get("segments")
    segs = [tuple(s) for s in segs] if segs else None
    res = detect(req["audio"], sr=req["sr"], frame=req["frame"], hop=req["hop"],
                 segments=segs)
    json.dump(res, open(sys.argv[2], "w"))

if __name__ == "__main__":
    main()
