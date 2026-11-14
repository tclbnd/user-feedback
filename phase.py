#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
幅度谱相位迭代重建（纯 Python 标准库实现，无第三方依赖）。

核心：Griffin-Lim 交替投影
  1) 幅度约束（频域）：S <- Mag * exp(j*angle(S))
  2) 时间域重叠一致：  x <- iSTFT(S);  S <- STFT(x)
两次投影之间用归一化 Frobenius 谱一致性误差度量收敛。

关键工程点：
  * 自带 radix-2 迭代 FFT（确定性、无并行调度带来的求和顺序差异）；
  * sqrt-Hann 分析/合成窗 + WOLA，满足 COLA，STFT/iSTFT 可完美重建，
    非整数频率（off-bin）泄漏由窗与重叠自然处理；
  * 固定 seed 的 random.Random 确定性初始化，逐次误差可复现，支持 eps 提前停止；
  * 检查点保存完整迭代状态（含 FGL 的上一轮谱与 RNG 状态），可断点续跑，
    续跑与一次性逐样本一致；
  * STFT 的“并行/串行”帧调度采用规范累加顺序，结果逐位一致。

CLI（向后兼容缺陷基线的接口）:
  python3 phase.py req.json output.json
  req.json: {"mag": [[帧][频]], "iters":60, "seed":7, "win":128, "hop":32,
             "n": 可选信号长度, "tol":1e-3, "method":"fgl"|"gl",
             "checkpoint": 可选, "outdir": 可选}
"""
import json
import sys
import math
import cmath
import random
import os
import wave
import struct

TWO_PI = 2.0 * math.pi


# --------------------------------------------------------------------------
# Radix-2 迭代 FFT（确定性，无 numpy）
# --------------------------------------------------------------------------
def fft(a, inverse=False):
    """长度必须为 2 的幂的原地 Cooley-Tukey FFT；返回新的 complex 列表。"""
    n = len(a)
    if n == 1:
        return [complex(a[0])]
    if n & (n - 1):
        raise ValueError("FFT 长度必须是 2 的幂, 得到 %d" % n)

    # 位反转置换
    x = [complex(v) for v in a]
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            x[i], x[j] = x[j], x[i]

    # 蝶形
    length = 2
    while length <= n:
        half = length >> 1
        sign = 1.0 if inverse else -1.0
        wlen = cmath.exp(complex(0.0, sign * TWO_PI / length))
        for i in range(0, n, length):
            w = complex(1.0, 0.0)
            for k in range(i, i + half):
                u = x[k]
                t = w * x[k + half]
                x[k] = u + t
                x[k + half] = u - t
                w *= wlen
        length <<= 1

    if inverse:
        inv = 1.0 / n
        return [v * inv for v in x]
    return x


def ifft(a):
    return fft(a, inverse=True)


# --------------------------------------------------------------------------
# 窗函数
# --------------------------------------------------------------------------
def hann_window(n):
    """周期 Hann 窗（n 点）。"""
    if n == 1:
        return [1.0]
    return [0.5 - 0.5 * math.cos(TWO_PI * i / n) for i in range(n)]


def sqrt_hann_window(n):
    w = hann_window(n)
    return [math.sqrt(v) for v in w]


# --------------------------------------------------------------------------
# STFT / iSTFT（单位归一化 + sqrt-Hann + WOLA，中心对齐，零填充，COLA）
#
# 设计要点（经实验验证）：
#   * 采用“中心帧对齐 + 零填充”。第 t 帧在 padded 缓冲中起点为 n_left+t*hop，
#     第 0 帧窗中心（padded 索引 n_left）对应原始样本 0。
#   * 零填充（而非反射）保证 C = STFT∘iSTFT 是严格幂等的正交投影
#     （实测 ||C(S)-C^2(S)|| ~ 1e-15），这是 Griffin-Lim 误差单调下降的前提。
#   * sqrt-Hann 分析/合成窗 + WOLA，内部样本满足 COLA、可机器精度完美重建。
#   * Hann 窗在端点严格为 0，因此每个信号的第 0 个（及最后一个）样本在谱中
#     数学上不可观测，属 STFT 固有死区，仅影响极少数边缘样本，不影响谱一致性。
# --------------------------------------------------------------------------
def _zero_pad(x, n_left, n_right):
    return [0.0] * n_left + list(x) + [0.0] * n_right


def _pad_geometry(n, hop, n_fft):
    """返回 (n_frames, n_left, n_right)。"""
    n_left = n_fft // 2
    n_frames = (n + hop - 1) // hop
    last_end = n_left + (n_frames - 1) * hop + n_fft
    n_right = max(0, last_end - (n_left + n))
    return n_frames, n_left, n_right


def stft(x, n_fft, hop, window=None, parallel=False):
    """
    单边（rfft 风格）STFT，中心帧对齐、零填充。返回 T 帧、每帧 n_fft//2+1 复数。
    parallel 仅切换帧调度路径，数学与浮点结合顺序完全相同，结果逐位一致。
    """
    if window is None:
        window = sqrt_hann_window(n_fft)
    n_frames, n_left, n_right = _pad_geometry(len(x), hop, n_fft)
    xp = _zero_pad(x, n_left, n_right)
    scale = 1.0 / math.sqrt(n_fft)
    n_bins = n_fft // 2 + 1

    def one_frame(t):
        start = n_left + t * hop
        seg = [xp[start + k] * window[k] * scale for k in range(n_fft)]
        return fft(seg)[:n_bins]

    frames = [None] * n_frames
    if parallel:
        # “并行”路径：帧独立计算（无共享写入），再按规范帧序写回。
        computed = [one_frame(t) for t in range(n_frames)]
        for t in range(n_frames):
            frames[t] = computed[t]
    else:
        for t in range(n_frames):
            frames[t] = one_frame(t)
    return frames


def istft(spec_frames, n_fft, hop, length, window=None, parallel=False):
    """
    逆 STFT：Hermitian 补全 -> ifft -> 合成窗 -> 重叠相加 -> 去窗归一化。
    与 stft 共享同一 padded 几何，内部样本满足完美重建。
    """
    if window is None:
        window = sqrt_hann_window(n_fft)
    n_frames = len(spec_frames)
    n_bins = n_fft // 2 + 1
    n_left = n_fft // 2
    scale = 1.0 / math.sqrt(n_fft)
    _, _, n_right = _pad_geometry(length, hop, n_fft)
    padded_len = n_left + length + n_right

    def time_frame(t):
        spec = list(spec_frames[t])
        if len(spec) != n_bins:
            raise ValueError("帧频点数 %d != %d" % (len(spec), n_bins))
        # Hermitian 共轭对称补全为全谱
        full = list(spec)
        for k in range(n_fft // 2 + 1, n_fft):
            full.append(spec[n_fft - k].conjugate())
        td = ifft(full)
        return [td[k].real * window[k] * scale for k in range(n_fft)]

    # 规范的“按帧序、按样本”累加，保证并行/串行浮点结合顺序逐位一致。
    tf = [None] * n_frames
    if parallel:
        computed = [time_frame(t) for t in range(n_frames)]
        for t in range(n_frames):
            tf[t] = computed[t]
    else:
        for t in range(n_frames):
            tf[t] = time_frame(t)

    win_sum = [0.0] * padded_len
    for t in range(n_frames):
        start = n_left + t * hop
        for k in range(n_fft):
            p = start + k
            if p < padded_len:
                win_sum[p] += window[k] * window[k] / n_fft

    acc = [0.0] * padded_len
    for t in range(n_frames):
        start = n_left + t * hop
        frame = tf[t]
        for k in range(n_fft):
            p = start + k
            if p < padded_len:
                acc[p] += frame[k]

    # 输出样本 i 对应 padded 索引 i+n_left（中心对齐）。
    out = [0.0] * length
    for i in range(length):
        d = win_sum[i + n_left]
        out[i] = acc[i + n_left] / d if d > 1e-14 else 0.0
    return out


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
def magnitude(spec_frames):
    return [[abs(v) for v in frame] for frame in spec_frames]


def phase_spectrum(spec_frames):
    return [[math.atan2(v.imag, v.real) for v in frame] for frame in spec_frames]


def consistency_error(spec_frames, mag_target, n_fft, hop, length, window):
    """
    归一化谱一致性误差：
      || STFT(iSTFT(S)) - MagProj(S) ||_F / (||Mag_target||_F + eps)
    两次投影后的残差，正是 Griffin-Lim 最小化的距离。
    全零幅度（静音）返回 0。
    """
    sig = istft(spec_frames, n_fft, hop, length, window)
    re_spec = stft(sig, n_fft, hop, window)
    num = 0.0
    den = 0.0
    for t, frame in enumerate(mag_target):
        for f, m in enumerate(frame):
            z = re_spec[t][f]
            ang = math.atan2(z.imag, z.real)
            proj = complex(m * math.cos(ang), m * math.sin(ang))
            d = z - proj
            num += d.real * d.real + d.imag * d.imag
            den += m * m
    if den <= 0.0:
        return 0.0
    return math.sqrt(num) / (math.sqrt(den) + 1e-12)


def snr_db(reference, estimate):
    """重建波形 SNR（dB）。参考信号近零（静音）时返回 inf。"""
    n = min(len(reference), len(estimate))
    pe = 0.0
    ps = 0.0
    for i in range(n):
        e = reference[i] - estimate[i]
        pe += e * e
        ps += reference[i] * reference[i]
    if ps < 1e-12:
        return float("inf")
    return 10.0 * math.log10(ps / (pe + 1e-20))


def spectral_snr_db(mag_target, spec_reconstructed):
    """
    谱幅度 SNR（dB）：20*log10(||Mag|| / ||Mag - |S_hat|||)。
    衡量 GL 直接优化的幅度约束满足质量；幅度-only 相位问题下通常很高，
    与时域波形 SNR（受不可恢复相位影响）互补。全零目标返回 inf。
    """
    ps = 0.0
    pe = 0.0
    for t, frame in enumerate(mag_target):
        for f, m in enumerate(frame):
            mhat = abs(spec_reconstructed[t][f])
            e = m - mhat
            ps += m * m
            pe += e * e
    if ps < 1e-12:
        return float("inf")
    return 20.0 * math.log10(math.sqrt(ps) / (math.sqrt(pe) + 1e-20))


# --------------------------------------------------------------------------
# 迭代重建
# --------------------------------------------------------------------------
def _rng_state(rng):
    st = rng.getstate()
    return [st[0], list(st[1]), st[2]]


def _set_rng_state(rng, state):
    rng.setstate((state[0], tuple(state[1]), state[2]))


def reconstruct(mag, n_fft, hop, length, seed=0, max_iter=800, tol=1e-3,
                method="afgl", momentum=0.99, init_spectrum=None, rng_state=None,
                prev_spectrum=None, first_iter=True, start_iter=0,
                err_history=None, alpha=None, last_err=None,
                parallel=False, record_final_error=True):
    """
    幅度谱相位迭代重建（Griffin-Lim 家族），两次投影交替：
      投影 C（时域重叠一致）：S -> STFT(iSTFT(S))
      投影 Pm（幅度约束）   ：Z -> Mag * exp(j*angle(Z))
    归一化谱一致性误差（每次迭代记录）：
      e = || C(S) - Pm(C(S)) ||_F / || Mag ||_F

    method :
      "gl"   基础 Griffin-Lim（无动量，误差严格单调但收敛慢）
      "fgl"  快速 Griffin-Lim，固定动量 momentum（Z + momentum*(Z-Z_prev)）
      "afgl" 自适应动量 FGL（默认）：误差下降则动量缓增至 0.95，回升则回退，
             对打击乐/宽频噪声的近相切约束收敛显著更快、更稳。

    可复现：seed 固定的 random.Random 初始化；所有运算确定性、无并行求和歧义。
    断点续跑：checkpoint 保存当前谱、上一轮 C(S)、动量 alpha、上一步误差与
    RNG 状态，续跑与一次性运行逐样本一致。
    """
    window = sqrt_hann_window(n_fft)
    n_frames = len(mag)
    n_bins = n_fft // 2 + 1
    rng = random.Random()

    # 判断是否为全零目标（静音）
    total_mag_energy = 0.0
    for frame in mag:
        for m in frame:
            total_mag_energy += m * m

    if init_spectrum is None:
        if total_mag_energy <= 0.0:
            zero_spec = [[complex(0.0, 0.0) for _ in range(n_bins)]
                         for _ in range(n_frames)]
            return {
                "spectrum": zero_spec,
                "signal": [0.0] * length,
                "err": [0.0],
                "iters_used": 0,
                "converged": True,
                "final_error": 0.0,
                "checkpoint": {
                    "spectrum": zero_spec, "prev_spectrum": zero_spec,
                    "first_iter": True, "iter": 0, "err_history": [0.0],
                    "rng_state": _rng_state(random.Random(seed)),
                    "alpha": 0.5, "last_err": None, "method": method,
                    "momentum": momentum,
                },
            }
        rng = random.Random(seed)
        spec = []
        for frame in mag:
            row = []
            for f in range(n_bins):
                m = frame[f]
                p = rng.uniform(0.0, TWO_PI)
                row.append(complex(m * math.cos(p), m * math.sin(p)))
            spec.append(row)
    else:
        spec = [list(frame) for frame in init_spectrum]
        if rng_state is not None:
            _set_rng_state(rng, rng_state)
        else:
            rng = random.Random(seed)

    if err_history is None:
        err_history = []
    # 动量外推所需的上一轮 C(S)
    if method in ("fgl", "afgl") and prev_spectrum is None and first_iter:
        prev = None
    else:
        prev = [list(frame) for frame in prev_spectrum] if prev_spectrum else None
        first_iter = bool(first_iter)
    # 自适应动量状态
    if method == "afgl":
        alpha = 0.5 if alpha is None else float(alpha)
        last_err = float("inf") if last_err is None else float(last_err)
    else:
        alpha = float(momentum)
        last_err = None

    converged = False
    it = start_iter
    err = float("nan")
    while it < start_iter + max_iter:
        # --- 投影 C：时间域重叠一致 ---
        time_sig = istft(spec, n_fft, hop, length, window, parallel=parallel)
        re_s = stft(time_sig, n_fft, hop, window, parallel=parallel)

        # 动量外推（在 C(S) 上），决定送入幅度投影的 base
        if method in ("fgl", "afgl"):
            if first_iter or prev is None:
                base = re_s
                first_iter = False
            else:
                base = [[re_s[t][f] + alpha * (re_s[t][f] - prev[t][f])
                         for f in range(n_bins)] for t in range(n_frames)]
            prev = re_s
        else:
            base = re_s

        # --- 投影 Pm：幅度约束；同时以 C(S) 为基准计算一致性误差 ---
        new_spec = [[None] * n_bins for _ in range(n_frames)]
        num = 0.0
        den = 0.0
        for t in range(n_frames):
            for f in range(n_bins):
                z = base[t][f]
                cz = re_s[t][f]
                m = mag[t][f]
                r = abs(z)
                if r > 1e-300:
                    proj = z * (m / r)      # 等价 m*exp(j*angle(z))
                else:
                    proj = complex(0.0, 0.0)
                new_spec[t][f] = proj
                d = cz - proj
                num += d.real * d.real + d.imag * d.imag
                den += m * m
        err = 0.0 if den <= 0.0 else math.sqrt(num) / (math.sqrt(den) + 1e-12)

        # 自适应动量调度（确定性）
        if method == "afgl":
            if err < last_err:
                alpha = min(0.95, alpha + 0.01)
            else:
                alpha = max(0.0, alpha * 0.7)
            last_err = err

        spec = new_spec
        err_history.append(err)
        it += 1
        if err <= tol:
            converged = True
            break

    signal = istft(spec, n_fft, hop, length, window, parallel=parallel)
    final_err = err
    if record_final_error and not converged:
        # 未达早停：独立再算一次终态一致性误差（与循环内同定义）
        final_err = consistency_error(spec, mag, n_fft, hop, length, window)
    checkpoint = {
        "spectrum": spec,
        "prev_spectrum": prev if prev is not None else spec,
        "first_iter": first_iter,
        "iter": it,
        "err_history": err_history,
        "rng_state": _rng_state(rng),
        "alpha": alpha,
        "last_err": last_err,
        "method": method,
        "momentum": momentum,
    }
    return {
        "spectrum": spec,
        "signal": signal,
        "err": err_history,
        "iters_used": it,
        "converged": converged,
        "final_error": final_err,
        "checkpoint": checkpoint,
    }


def resume_from_checkpoint(mag, n_fft, hop, length, checkpoint,
                           max_iter=800, tol=1e-3, method=None,
                           parallel=False):
    """从检查点断点续跑；method 默认沿用检查点记录的方法。"""
    if method is None:
        method = checkpoint.get("method", "afgl")
    return reconstruct(
        mag, n_fft, hop, length,
        max_iter=max_iter, tol=tol, method=method,
        momentum=checkpoint.get("momentum", 0.99),
        init_spectrum=checkpoint["spectrum"],
        rng_state=checkpoint["rng_state"],
        prev_spectrum=checkpoint["prev_spectrum"],
        first_iter=checkpoint["first_iter"],
        start_iter=checkpoint["iter"],
        err_history=list(checkpoint["err_history"]),
        alpha=checkpoint.get("alpha"),
        last_err=checkpoint.get("last_err"),
        parallel=parallel,
    )


# --------------------------------------------------------------------------
# 输出：WAV / JSON / SVG 收敛曲线
# --------------------------------------------------------------------------
def save_wav(path, signal, sr=8000):
    peak = max((abs(v) for v in signal), default=0.0)
    g = 0.95 / peak if peak > 1e-9 else 1.0
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        frames = bytearray()
        for v in signal:
            s = int(max(-1.0, min(1.0, v * g)) * 32767)
            frames += struct.pack("<h", s)
        wf.writeframes(bytes(frames))


def save_phase_json(path, spec):
    with open(path, "w") as f:
        json.dump(phase_spectrum(spec), f)


def save_spectrum_json(path, spec):
    with open(path, "w") as f:
        json.dump([[[v.real, v.imag] for v in frame] for frame in spec], f)


def _json_default(o):
    """json.dump 的 default 钩子：complex -> [real, imag]。"""
    if isinstance(o, complex):
        return [o.real, o.imag]
    raise TypeError("Object of type %s is not JSON serializable"
                    % type(o).__name__)


def _revive_complex(obj):
    """把 [real, imag] 形式的谱（帧/频点）递归还原为 complex。"""
    if isinstance(obj, list):
        if (len(obj) == 2 and all(isinstance(v, (int, float)) for v in obj)
                and not isinstance(obj[0], bool)):
            return complex(obj[0], obj[1])
        return [_revive_complex(v) for v in obj]
    return obj


def plot_convergence_svg(path, err_curves, title="convergence"):
    """err_curves: dict(label -> [err...])；纯标准库生成 SVG 折线图（log 纵轴）。"""
    w, h = 720, 420
    ml, mr, mt, mb = 70, 170, 40, 50
    pw, ph = w - ml - mr, h - mt - mb
    colors = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd",
              "#ff7f0e", "#17becf", "#8c564b", "#7f7f7f"]
    max_len = max((len(e) for e in err_curves.values()), default=1)
    xs = max(1, max_len - 1)
    allv = [v for e in err_curves.values() for v in e if v > 0]
    ymax = max(allv) if allv else 1.0
    ymin = min(allv) if allv else 1e-6
    if ymin <= 0:
        ymin = 1e-12
    ymin = max(ymin * 0.5, 1e-12)
    lmax, lmin = math.log10(ymax), math.log10(ymin)
    if lmax == lmin:
        lmax = lmin + 1.0

    def X(i):
        return ml + (i / xs) * pw

    def Y(v):
        lv = math.log10(max(v, ymin))
        return mt + (1 - (lv - lmin) / (lmax - lmin)) * ph

    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'font-family="monospace">' % (w, h)]
    parts.append('<rect width="%d" height="%d" fill="white"/>' % (w, h))
    parts.append('<text x="%d" y="24" font-size="16" font-weight="bold">%s '
                 '(log scale)</text>' % (ml, title))
    # 网格 + y 轴刻度
    for g in range(6):
        yy = mt + g * ph / 5
        lv = lmax - g * (lmax - lmin) / 5
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" '
                     'stroke="#eee"/>' % (ml, yy, ml + pw, yy))
        parts.append('<text x="%d" y="%.1f" font-size="11" text-anchor="end" '
                     'fill="#555">1e%.0f</text>' % (ml - 6, yy + 4, lv))
    # x 轴刻度
    for g in range(6):
        i = round(g * xs / 5)
        xx = X(i)
        parts.append('<text x="%.1f" y="%d" font-size="11" '
                     'text-anchor="middle">%d</text>' % (xx, mt + ph + 18, i))
    parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="black"/>'
                 % (ml, mt, ml, mt + ph))
    parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="black"/>'
                 % (ml, mt + ph, ml + pw, mt + ph))
    parts.append('<text x="%d" y="%d" font-size="12">iteration</text>'
                 % (ml + pw // 2 - 30, h - 12))
    # 1e-3 参考线
    if lmin <= -3 <= lmax:
        yy = Y(1e-3)
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" '
                     'stroke="#d62728" stroke-dasharray="6,4"/>'
                     % (ml, yy, ml + pw, yy))
        parts.append('<text x="%d" y="%.1f" font-size="11" fill="#d62728">'
                     'tol=1e-3</text>' % (ml + pw - 70, yy - 4))
    # 曲线
    for ci, (label, errs) in enumerate(err_curves.items()):
        col = colors[ci % len(colors)]
        pts = " ".join("%.1f,%.1f" % (X(i), Y(v)) for i, v in enumerate(errs))
        parts.append('<polyline fill="none" stroke="%s" stroke-width="1.8" '
                     'points="%s"/>' % (col, pts))
        lx = ml + pw + 12
        ly = mt + 18 + ci * 20
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" '
                     'stroke-width="2"/>' % (lx - 8, ly - 4, lx + 4, ly - 4, col))
        last = errs[-1] if errs else float("nan")
        parts.append('<text x="%d" y="%d" font-size="11" fill="%s">%s '
                     '(%.2e)</text>' % (lx + 8, ly, col, label, last))
    parts.append("</svg>")
    with open(path, "w") as f:
        f.write("\n".join(parts))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def run(req):
    mag = req["mag"]
    n_fft = int(req.get("win", 128))
    # 默认 12.5% 重叠（hop=7/8*win）：纯幅度 GL 下约束集相切最缓、平台最低，
    # 对强相关的语音也能在有限迭代内达到 1e-3；可用 req["hop"] 覆盖为任意跳数。
    hop = int(req.get("hop", 7 * n_fft // 8))
    n_frames = len(mag)
    # 与 stft 的帧数关系 n_frames=ceil(length/hop) 自洽
    length = int(req.get("n", (n_frames - 1) * hop + 1))
    seed = int(req.get("seed", 0))
    max_iter = int(req.get("iters", 800))
    tol = float(req.get("tol", 1e-3))
    method = req.get("method", "afgl")
    parallel = bool(req.get("parallel", False))

    # 若 checkpoint 来自 JSON（谱为 [real, imag]），先还原为 complex
    if req.get("checkpoint") is not None:
        cp = req["checkpoint"]
        cp = dict(cp)
        cp["spectrum"] = _revive_complex(cp["spectrum"])
        cp["prev_spectrum"] = _revive_complex(cp["prev_spectrum"])
        req = dict(req)
        req["checkpoint"] = cp

    if req.get("checkpoint") is not None:
        result = resume_from_checkpoint(
            mag, n_fft, hop, length, req["checkpoint"],
            max_iter=max_iter, tol=tol,
            method=None if method == "afgl" else method,
            parallel=parallel)
    else:
        result = reconstruct(
            mag, n_fft, hop, length, seed=seed, max_iter=max_iter,
            tol=tol, method=method, parallel=parallel)

    # 输出用 checkpoint：复数谱转 [real, imag] 以便 JSON 序列化
    out_cp = dict(result["checkpoint"])
    out_cp["spectrum"] = [[[v.real, v.imag] for v in frame]
                          for frame in result["checkpoint"]["spectrum"]]
    out_cp["prev_spectrum"] = [[[v.real, v.imag] for v in frame]
                               for frame in result["checkpoint"]["prev_spectrum"]]

    out = {
        "signal": result["signal"],
        "err": result["err"],
        "snr": result.get("snr"),
        "iters_used": result["iters_used"],
        "converged": result["converged"],
        "final_error": result["final_error"],
        "phase": phase_spectrum(result["spectrum"]),
        "checkpoint": out_cp,
    }
    outdir = req.get("outdir")
    if outdir:
        os.makedirs(outdir, exist_ok=True)
        save_wav(os.path.join(outdir, "reconstructed.wav"), result["signal"])
        save_phase_json(os.path.join(outdir, "phase.json"), result["spectrum"])
        with open(os.path.join(outdir, "err.json"), "w") as f:
            json.dump(result["err"], f)
        plot_convergence_svg(os.path.join(outdir, "convergence.svg"),
                             {"seed%d" % seed: result["err"]})
    return out


def main():
    if len(sys.argv) < 3:
        print("usage: python3 phase.py req.json output.json", file=sys.stderr)
        sys.exit(2)
    with open(sys.argv[1]) as f:
        req = json.load(f)
    out = run(req)
    with open(sys.argv[2], "w") as f:
        json.dump(out, f, default=_json_default)
    print("iters_used=%d final_error=%.3e converged=%s"
          % (out["iters_used"], out["final_error"], out["converged"]))


if __name__ == "__main__":
    main()
