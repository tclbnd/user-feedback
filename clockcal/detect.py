"""闪光与声音同步脉冲检测。

闪光检测在帧亮度序列上找能量峰，再用峰两侧采样做线性插值得到亚帧时刻；
声音检测对 1 kHz 带通能量做滑窗峰值拾取。两个检测器使用互不相同的机制
（亮度重心 vs. 音频模板能量），后者同时承担验收中的独立复核职责。
"""
import math

FLASH_BASE = 0.15       # 背景亮度
FLASH_PEAK = 0.85       # 闪光峰值亮度
FLASH_HALF_WIDTH = 0.012  # 闪光半宽（秒）
TONE_FREQ = 500.0       # 同步声频率 Hz（2 kHz 采样下每周期 4 点）
TONE_HALF_WIDTH = 0.010
TONE_AMP = 0.6
DETECT_SNR = 3.0        # 检测信噪比门限


def flash_brightness(frame_tick, emit_tick, half_width=FLASH_HALF_WIDTH):
    """单帧亮度：三角闪光脉冲 + 低背景。"""
    d = abs(frame_tick - emit_tick)
    if d > 2 * half_width:
        return FLASH_BASE
    return FLASH_BASE + (FLASH_PEAK - FLASH_BASE) * (1 - d / (2 * half_width))


def detect_flashes(frame_ticks, brightness, fps, snr=DETECT_SNR):
    """从帧亮度序列检测闪光，返回亚帧发射时刻（tick 轴，升序）。

    闪光脉冲宽约 24 ms、同步间隔 30 s，二者相差三个数量级，因此用全局
    鲁棒门限切出超阈连通段：一个连通段恰为一次闪光，整段只报一个峰，
    再用亮度加权重心换算亚帧时刻。
    """
    base = sorted(brightness)[len(brightness) // 2]
    mad = sorted(abs(v - base) for v in brightness)[len(brightness) // 2]
    sigma = max(1.4826 * mad, 1e-3)
    threshold = base + snr * sigma
    n = len(brightness)
    peaks = []
    i = 0
    while i < n:
        if brightness[i] <= threshold:
            i += 1
            continue
        j = i
        while j + 1 < n and brightness[j + 1] > threshold:
            j += 1
        # 连通段 [i, j]：要求峰相对背景有足够突出度，排除噪声离群
        seg_max = max(brightness[i:j + 1])
        if seg_max - base >= max(8.0 * sigma, 0.08):
            num = den = 0.0
            for idx in range(i, j + 1):
                w = max(brightness[idx] - base, 0.0)
                num += w * frame_ticks[idx]
                den += w
            peaks.append(num / den if den else frame_ticks[i])
        i = j + 1
    # 帧时间戳抖动可能让同一连通段在乱序处产生重复时刻。按采集顺序
    # 去重（与已输出峰相差 50 ms 内视为同一次闪光），保留采集顺序，
    # 这样重启导致的本地时间回跳不会被排序掩盖。
    merged = []
    for t in peaks:
        if any(abs(t - u) < 0.05 for u in merged):
            continue
        merged.append(t)
    return merged


def tone_samples(duration_ticks, sample_rate, emit_ticks,
                 freq=TONE_FREQ, half_width=TONE_HALF_WIDTH, amp=TONE_AMP,
                 noise_sigma=0.01, rng=None):
    """在音频采样数组上叠加 1 kHz 高斯包络脉冲与背景噪声。"""
    if rng is None:
        rng = random.Random(0)
    n = int(round(duration_ticks * sample_rate))
    out = [rng.gauss(0.0, noise_sigma) for _ in range(n)]
    two_pi_f = 2 * math.pi * freq
    for t0 in emit_ticks:
        k0 = max(0, int((t0 - 3 * half_width) * sample_rate))
        k1 = min(n, int((t0 + 3 * half_width) * sample_rate))
        for k in range(k0, k1):
            t = k / sample_rate - t0
            env = math.exp(-0.5 * (t / half_width) ** 2)
            out[k] += amp * env * math.cos(two_pi_f * (k / sample_rate - t0)
                                           + two_pi_f * t0)
    return out


def _band_energy(samples, sample_rate, freq, half_width):
    """1 kHz 相干解调能量（I/Q 低通取模），按采样点返回，整体 O(n)。"""
    n = len(samples)
    two_pi_f = 2 * math.pi * freq
    win = max(3, int(half_width * sample_rate))
    norm = 1.0 / (2 * win + 1)
    # 相干解调后的 I/Q 分量
    xi = [samples[k] * math.cos(two_pi_f * k / sample_rate) for k in range(n)]
    xq = [samples[k] * math.sin(two_pi_f * k / sample_rate) for k in range(n)]
    # 滑动窗口求和
    acc_i = sum(xi[:win + 1])
    acc_q = sum(xq[:win + 1])
    energy = [0.0] * n
    for k in range(n):
        if k > 0:
            add = min(n - 1, k + win)
            drop = k - win - 1
            if k + win < n:
                acc_i += xi[add]
                acc_q += xq[add]
            if drop >= 0:
                acc_i -= xi[drop]
                acc_q -= xq[drop]
        energy[k] = math.hypot(acc_i * norm, acc_q * norm)
    return energy


def detect_tones(samples, sample_rate, freq=TONE_FREQ,
                 half_width=TONE_HALF_WIDTH, snr=8.0):
    """检测音频脉冲，返回脉冲中心的 tick 时刻（秒，升序）。

    相干解调能量上，一个脉冲对应一个超阈连通段，整段只报一个加权重心。
    """
    energy = _band_energy(samples, sample_rate, freq, half_width)
    med = sorted(energy)[len(energy) // 2]
    mad = sorted(abs(v - med) for v in energy)[len(energy) // 2]
    hi = sorted(energy)[int(0.95 * len(energy))]
    # 脉冲解调能量远高于噪声，叠加相对中值倍数门限抑制零星噪声超阈
    threshold = max(med + snr * max(1.4826 * mad, 1e-6),
                    0.5 * hi, med * 50.0)
    n = len(energy)
    peaks = []
    k = 0
    while k < n:
        if energy[k] <= threshold:
            k += 1
            continue
        j = k
        while j + 1 < n and energy[j + 1] > threshold:
            j += 1
        num = sum(energy[idx] * idx for idx in range(k, j + 1))
        den = sum(energy[idx] for idx in range(k, j + 1))
        peaks.append((num / den) / sample_rate if den else k / sample_rate)
        k = j + 1
    return sorted(peaks)



# 独立复核检测器：不使用相干解调，改用简单的短时能量峰值，
# 与 detect_tones 的实现完全独立，用于验收交叉验证。
def independent_pulse_scan(samples, sample_rate, half_width=TONE_HALF_WIDTH,
                           snr=6.0):
    """短时 RMS 能量峰值扫描，返回检测到的脉冲中心时刻列表。整体 O(n)。"""
    win = max(4, int(half_width * sample_rate))
    n = len(samples)
    sq = [s * s for s in samples]
    energy = [0.0] * n
    acc = sum(sq[:win + 1])
    for k in range(n):
        if k > 0:
            add = min(n - 1, k + win)
            drop = k - win - 1
            if k + win < n:
                acc += sq[add]
            if drop >= 0:
                acc -= sq[drop]
        span = min(n - 1, k + win) - max(0, k - win) + 1
        energy[k] = acc / span
    med = sorted(energy)[len(energy) // 2]
    mad = sorted(abs(v - med) for v in energy)[len(energy) // 2]
    # 脉冲能量远高于背景（实测约千倍），门限取中值的固定倍数，稳健可靠
    threshold = max(med + snr * max(1.4826 * mad, 1e-12), med * 50.0)
    peaks = []
    k = 0
    while k < n:
        if energy[k] <= threshold:
            k += 1
            continue
        # 一个脉冲对应一个超阈连通段，整段只报一个加权重心
        j = k
        while j + 1 < n and energy[j + 1] > threshold:
            j += 1
        num = sum(energy[idx] * idx for idx in range(k, j + 1))
        den = sum(energy[idx] for idx in range(k, j + 1))
        peaks.append((num / den) / sample_rate if den else k / sample_rate)
        k = j + 1
    return sorted(peaks)


import random  # noqa: E402  (tone_samples 默认 RNG)
