# -*- coding: utf-8 -*-
"""
纯标准库 DSP 工具: 基-2 迭代 FFT、窗函数、Welch PSD、带通功率、THD/THD+N。
功率约定: 满幅正弦峰值 1.0 => 0 dBFS。PSD 单位 dBFS/Hz,
采用单边谱并按 ENBW 归一化, 平坦噪声的带内积分功率与理论值一致。
"""
import math


# ----------------------------- 基-2 FFT/IFFT -----------------------------
def fft(a):
    """迭代基-2 时间抽取 FFT, 输入 list[complex/float], 长度需为 2 的幂。"""
    n = len(a)
    if n & (n - 1):
        raise ValueError("FFT 长度必须是 2 的幂")
    x = [complex(v) for v in a]
    # 位反转置换
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            x[i], x[j] = x[j], x[i]
    length = 2
    while length <= n:
        ang = -2.0 * math.pi / length
        wlen = complex(math.cos(ang), math.sin(ang))
        half = length >> 1
        for i in range(0, n, length):
            w = 1.0 + 0.0j
            for k in range(i, i + half):
                u = x[k]
                v = x[k + half] * w
                x[k] = u + v
                x[k + half] = u - v
                w *= wlen
        length <<= 1
    return x


def rfft_mag(x):
    """返回单边幅度谱 |X[k]|, k=0..N/2。"""
    n = len(x)
    spec = fft(x)
    return [abs(spec[k]) for k in range(n // 2 + 1)]


# ------------------------------- 窗函数 --------------------------------
def window(n, name):
    if name == "boxcar":
        return [1.0] * n
    if name == "hann":
        return [0.5 - 0.5 * math.cos(2.0 * math.pi * i / (n - 1))
                for i in range(n)]
    if name == "blackman":
        return [0.42 - 0.5 * math.cos(2.0 * math.pi * i / (n - 1))
                + 0.08 * math.cos(4.0 * math.pi * i / (n - 1))
                for i in range(n)]
    raise ValueError("unknown window: " + str(name))


def enbw(w):
    """等效噪声带宽(单位: bin)。"""
    s2 = sum(v * v for v in w)
    s1 = sum(w)
    return n_bin_enbw(w, s1, s2)


def n_bin_enbw(w, s1, s2):
    return len(w) * s2 / (s1 * s1)


# ------------------------------ Welch PSD ------------------------------
def welch_psd(x, nfft, win_name, fs, overlap=0.5):
    """
    单边 Welch 功率谱密度, 线性单位 (电平^2 / Hz), 长度 nfft//2+1。
    缩放: PSD[k] = mean(|X|^2) / (fs * sum(w^2)), 再对单边频带乘 2(DC/Nyq 除外)。
    """
    step = int(round(nfft * (1.0 - overlap)))
    w = window(nfft, win_name)
    w2 = sum(v * v for v in w)
    nseg = 1 + (len(x) - nfft) // step if len(x) >= nfft else 0
    if nseg <= 0:
        raise ValueError("数据短于一个 FFT 段")
    nb = nfft // 2 + 1
    acc = [0.0] * nb
    for s in range(nseg):
        base = s * step
        seg = [x[base + i] * w[i] for i in range(nfft)]
        spec = fft(seg)
        for k in range(nb):
            acc[k] += (spec[k].real ** 2 + spec[k].imag ** 2) / w2
    scale = 1.0 / (nseg * fs)
    psd = [v * scale for v in acc]
    for k in range(1, nb - 1):
        psd[k] *= 2.0
    freqs = [fs * k / nfft for k in range(nb)]
    return freqs, psd


def band_power(freqs, psd, f_lo, f_hi):
    """梯形积分 PSD 得到带内功率(电平^2)。"""
    p = 0.0
    for k in range(1, len(freqs)):
        if freqs[k] <= f_lo or freqs[k - 1] >= f_hi:
            continue
        a = max(f_lo, freqs[k - 1])
        b = min(f_hi, freqs[k])
        if b > a:
            p += 0.5 * (psd[k - 1] + psd[k]) * (b - a)
    return p


def band_psd_db(freqs, psd, f_lo, f_hi):
    """带内平均 PSD, dBFS/Hz。"""
    p = band_power(freqs, psd, f_lo, f_hi)
    return 10.0 * math.log10(p / (f_hi - f_lo))


def full_band_power(freqs, psd):
    return band_power(freqs, psd, freqs[0], freqs[-1])


# ----------------------------- THD / THD+N -----------------------------
def nearest_bin(freqs, f):
    best = 0
    bd = abs(freqs[0] - f)
    for k in range(len(freqs)):
        d = abs(freqs[k] - f)
        if d < bd:
            bd, best = d, k
    return best


def coherent_thdn(x, fs, f0, harmonics=10):
    """
    相干正交投影下的 THD / THD+N（不要求 FFT bin 对齐, 无窗泄漏）。
    返回 dict: 基波与各谐波 dBFS、THD_dB、THD+N_dB、噪声功率等。
    基波/谐波功率由逐频 sin/cos 正交投影精确得到;
    噪声功率 = 总功率 - 基波 - 谐波。
    """
    n = len(x)
    p_sig = sum(v * v for v in x) / n

    h_powers = []
    for h in range(1, harmonics + 1):
        f = f0 * h
        if f >= fs / 2.0:
            break
        h_powers.append((h, f, coherent_power_at(x, fs, f)))

    p_fund = h_powers[0][2]
    p_harm = sum(item[2] for item in h_powers[1:])
    p_noise = max(0.0, p_sig - p_fund - p_harm)

    def dbfs(p):
        return 10.0 * math.log10(max(p, 1e-20))

    def ratio_db(num, den):
        if den <= 0.0:
            return float("-inf")
        return 10.0 * math.log10(max(num, 1e-20) / den)

    return {
        "fundamental_dbfs": dbfs(p_fund),
        "harmonics": [(h, f, dbfs(p)) for (h, f, p) in h_powers],
        "thd_db": ratio_db(p_harm, p_fund),
        "thd_plus_n_db": ratio_db(p_harm + p_noise, p_fund),
        "noise_floor_dbfs": dbfs(p_noise),
        "p_fund": p_fund,
        "p_harm": p_harm,
        "p_noise": p_noise,
    }


def spectral_peaks_db(x, fs, nfft, win_name, f0, harmonics=10):
    """
    非相干情况下用窗函数 DFT 直接读谐波峰值(dBFS, 已按相干增益修正)。
    作为 coherent_thdn 的交叉验证。
    """
    w = window(nfft, win_name)
    cg = sum(w) / nfft
    seg = x[:nfft]
    spec = fft([seg[i] * w[i] for i in range(nfft)])
    df = fs / nfft
    out = []
    for h in range(1, harmonics + 1):
        f = f0 * h
        if f >= fs / 2.0:
            break
        k = int(round(f / df))
        p = 2.0 * (spec[k].real ** 2 + spec[k].imag ** 2) / (nfft * nfft) / (cg * cg)
        out.append((h, f, 10.0 * math.log10(max(p, 1e-20))))
    return out


def estimate_tone(x, fs, f0):
    """相干正交投影估计单频正弦幅度与相位, 不要求 f0 落在 FFT bin 上。
    对长度 N 序列, 用 sin/cos 基做最小二乘投影(Goertzel 思想):
      amp sin(w n + ph) = I sin(w n) + Q cos(w n), I=amp cos ph, Q=amp sin ph
    当 N 含近似整数周期时 sin/cos 正交性良好。返回 amp, phase。"""
    n = len(x)
    w = 2.0 * math.pi * f0 / fs
    sw = math.sin(w)
    cw = math.cos(w)
    sn = 0.0
    cn = 1.0
    acc_s = 0.0   # sum x*sin
    acc_c = 0.0   # sum x*cos
    norm_s = 0.0
    norm_c = 0.0
    for i in range(n):
        xi = x[i]
        acc_s += xi * sn
        acc_c += xi * cn
        norm_s += sn * sn
        norm_c += cn * cn
        sn, cn = sn * cw + cn * sw, cn * cw - sn * sw
    i_coef = acc_s / norm_s
    q_coef = acc_c / norm_c
    amp = math.hypot(i_coef, q_coef)
    phase = math.atan2(q_coef, i_coef)
    return amp, phase


def project_and_subtract(x, fs, freqs):
    """对 freqs 列表中的每个频率做相干正弦投影并从 x 中减去, 返回残差。
    逐频独立投影; 对相干采样(整数周期)各频率正交, 可精确剔除基波与谐波。"""
    n = len(x)
    subs = [0.0] * n
    for f0 in freqs:
        amp, phase = estimate_tone(x, fs, f0)
        w = 2.0 * math.pi * f0 / fs
        sw = math.sin(w)
        cw = math.cos(w)
        sn, cn = 0.0, 1.0
        for i in range(n):
            subs[i] += amp * (math.cos(phase) * sn + math.sin(phase) * cn)
            sn, cn = sn * cw + cn * sw, cn * cw - sn * sw
    return [x[i] - subs[i] for i in range(n)]


def coherent_power_at(x, fs, f0):
    """精确频率 f0 处的单边正弦功率(电平^2), 相干正交投影, 与 bin 对齐无关。"""
    amp, _ = estimate_tone(x, fs, f0)
    return amp * amp / 2.0


def residual_after_tone(x, fs, f0, harmonics=0):
    """重建并减去基波(可选前若干谐波), 返回纯噪声残差。
    用相干正交投影, 不依赖 FFT bin 对齐, 消除主信号对噪声 PSD 的污染。"""
    freqs = [f0 * h for h in range(1, harmonics + 2)]
    return project_and_subtract(x, fs, freqs)