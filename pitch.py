"""逐帧基频跟踪（YIN + 八度纠偏 + 两阶段有声/无声判定）。

实现要点
--------
1. 差分函数 d(tau) 用自相关推导（纯标准库，无第三方依赖），再做累积均值
   归一化得到 YIN 的 d'(tau)（CMND），阈值 0.15 判定无声/弱信号段。
2. 八度纠偏：YIN 原始的“首个低于阈值的谷”会在强二次谐波时给出倍频
   （周期减半）。这里在确认主周期的同时保留 2τ/3τ 的亚谐波谷证据，仅当
   亚谐波谷同样足够深、且与主周期解释相容时才降到更低基频，避免滑音中
   的倍频程跳变；每帧输出 0–1 的置信度（1 - d'(τ*)）。
3. 输出逐帧 offset/voiced/hz/confidence、有声与无声计数及相邻有声帧的
   稳定性统计，并给出与帧长无关的归一化摘要 digest。

边缘帧规则（与帧长选择保持统一）
--------------------------------
* 采样从索引 0 开始，每 hop_size 移动，只保留含 frame_size 个真实采样的
  完整帧：offset in range(0, N - frame_size + 1, hop_size)，不做补零。
* 帧以窗口中心对齐原始时间 center = offset + (frame_size - 1) / 2，
  因此更换帧长后仍按各自窗口中心对应同一时间点。
* 基频工作范围固定 70–600 Hz；τ 的搜索范围按该范围与帧长共同裁剪，
  超出范围的谷不参与判定。
"""

import hashlib
import json
import math
from math import sqrt
from operator import mul
from pathlib import Path
import sys

F_MIN = 70.0
F_MAX = 600.0
YIN_THRESHOLD = 0.15          # CMND 主阈值（静态段）
YIN_THRESHOLD_RELAX = 0.35    # 宽松阈值（滑音等非平稳但仍周期的段）
DIP_MARGIN = 1.7              # 亚谐波谷相对主谷允许的深度余量
HARMONIC_RATIOS = (2.0, 3.0)  # 保留的亚谐波证据（τ 的整数倍）
RMS_FLOOR = 2.0e-4            # 绝对弱信号门限（归一化满量程下）
NOISE_REL = 2.2               # 能量门限 = NOISE_REL × 噪声段 RMS 中位数
CONTEXT = 3                   # 孤立无声插补的左右上下文（帧）


def _autocorrelation(x, taumax):
    """有偏自相关 r(tau) = sum_{j=0}^{W-1-tau} x[j] x[j+tau]。"""
    w = len(x)
    return [sum(map(mul, x[:w - tau], x[tau:w])) for tau in range(taumax + 1)]


def difference_function(frame, taumax):
    """YIN 差分函数 d(tau)。

    d(tau) = r(0,t)+r(tau,0)-2 r(tau)，其中 r(0,0) 为帧能量。
    浮点误差产生的微小负值裁剪为 0。
    """
    w = len(frame)
    r = _autocorrelation(frame, taumax)
    out = [0.0] * (taumax + 1)
    for tau in range(1, taumax + 1):
        # 两段各自的零滞后能量
        e0 = sum(map(mul, frame[:w - tau], frame[:w - tau]))
        et = sum(map(mul, frame[tau:w], frame[tau:w]))
        d = e0 + et - 2.0 * r[tau]
        out[tau] = d if d > 0.0 else 0.0
    out[0] = r[0]
    return out


def cumulative_mean_normalized(diff):
    """累积均值归一化差分 d'(tau)；d'(0) 固定为 1。"""
    cmnd = [1.0] * len(diff)
    running = 0.0
    for tau in range(1, len(diff)):
        running += diff[tau]
        if running <= 0.0:
            cmnd[tau] = 1.0
        else:
            cmnd[tau] = diff[tau] * tau / running
    return cmnd


def _parabolic_interp(y, tau):
    """对离散谷做抛物线插值，返回分数滞后与插值后的 d'。"""
    if tau <= 0 or tau >= len(y) - 1:
        return float(tau), y[tau]
    a, b, c = y[tau - 1], y[tau], y[tau + 1]
    denom = a + c - 2.0 * b
    if denom == 0.0:
        return float(tau), b
    shift = 0.5 * (a - c) / denom
    shift = max(-1.0, min(1.0, shift))
    refined = tau + shift
    interp = b - 0.25 * (a - c) * shift
    return refined, max(0.0, interp)


def _local_minima(cmnd, tau_min, tau_max):
    """严格局部最小（含平台取中点），返回 (整数 tau, d') 列表。"""
    mins = []
    tau = max(tau_min, 2)
    upper = min(tau_max, len(cmnd) - 2)
    while tau <= upper:
        if cmnd[tau] < cmnd[tau - 1] and cmnd[tau] <= cmnd[tau + 1]:
            start = tau
            while tau < upper and cmnd[tau + 1] == cmnd[tau]:
                tau += 1
            mins.append(((start + tau) // 2, cmnd[(start + tau) // 2]))
        tau += 1
    return mins


def refine_period(cmnd, tau_min, tau_max):
    """阈值判定 + 八度纠偏，返回候选信息字典。

    步骤：
      a. 取 d' 中最低谷 τ_best；保留其周期证据（即便高于主阈值，
         供宽松阈值/滑音段二次判定使用）。
      b. 优先采纳“首个低于阈值的谷”（低周期解释，抑制倍频误报）。
      c. 检查 2τ、3τ 处的亚谐波谷：仅当其深度与主谷相容
         （≤ DIP_MARGIN 倍且本身低于阈值）时才降周期，且只降一次，
         证据保留在 subharmonic 中供音轨级复核与置信度审计。
    """
    minima = _local_minima(cmnd, tau_min, tau_max)
    result = dict(tau=None, dprime=1.0, confidence=0.0,
                  below_threshold=False, relaxed=False,
                  best_dprime=1.0, subharmonic=[])
    if not minima:
        return result
    tau_best, d_best = min(minima, key=lambda p: p[1])
    result["best_dprime"] = d_best

    # 低周期优先：第一个低于主阈值的谷
    tau0 = next((t for t, d in minima if d < YIN_THRESHOLD), None)
    if tau0 is None:
        # 没有过硬阈值的谷：记录最低谷作为宽松候选，不立即定有声
        tau_frac, d_interp = _parabolic_interp(cmnd, tau_best)
        result.update(tau=tau_frac, dprime=d_interp,
                      confidence=max(0.0, min(1.0, 1.0 - d_interp)),
                      relaxed=d_best < YIN_THRESHOLD_RELAX)
        return result

    chosen = tau0
    evidence = []
    for ratio in HARMONIC_RATIOS:
        cand = int(round(tau0 * ratio))
        if cand > tau_max:
            break
        near = [p for p in minima if abs(p[0] - cand) <= 1]
        if not near:
            continue
        tc, dc = min(near, key=lambda p: p[1])
        evidence.append((tc, dc))
        # 亚谐波必须本身可信且与主谷相容，防止把噪声谷当成低基频
        if chosen == tau0 and dc < YIN_THRESHOLD and dc <= DIP_MARGIN * cmnd[tau0]:
            chosen = tc

    tau_frac, d_interp = _parabolic_interp(cmnd, chosen)
    result.update(
        tau=tau_frac, dprime=d_interp, below_threshold=True,
        confidence=max(0.0, min(1.0, 1.0 - d_interp)),
        subharmonic=evidence,
    )
    return result


def _rms(frame):
    return sqrt(sum(map(mul, frame, frame)) / len(frame))


def analyze(samples, rate, frame_size, hop_size):
    n = len(samples)
    if frame_size <= 0 or hop_size <= 0 or n < frame_size:
        frames, stats = _stability([], rate, frame_size, hop_size)
        return dict(frames=frames, stats=stats, digest=_digest(frames, stats))

    tau_min = max(2, int(rate / F_MAX))
    tau_max = min(frame_size - 2, int(rate / F_MIN))

    # ---- 第一阶段：逐帧 YIN 测量，保留候选与置信度 ----
    records = []  # dict(offset, rms, tau, dprime, conf, strong, relaxed)
    rms_values = []
    for offset in range(0, n - frame_size + 1, hop_size):
        frame = samples[offset:offset + frame_size]
        rms = _rms(frame)
        rms_values.append(rms)
        rec = dict(offset=offset, rms=rms, tau=None, dprime=1.0,
                   conf=0.0, strong=False, relaxed=False)
        if tau_max > tau_min and rms >= RMS_FLOOR:
            diff = difference_function(frame, tau_max)
            cmnd = cumulative_mean_normalized(diff)
            cand = refine_period(cmnd, tau_min, tau_max)
            tau_frac = cand["tau"]
            if tau_frac is not None:
                hz = rate / tau_frac
                if F_MIN <= hz <= F_MAX:
                    rec.update(tau=tau_frac, dprime=cand["dprime"],
                               conf=cand["confidence"],
                               strong=cand["below_threshold"],
                               relaxed=cand["relaxed"])
        records.append(rec)

    # ---- 第二阶段：能量门限 ----
    # 噪声底取帧 RMS 的低四分位（静音/噪声段聚集在低端），门限为其
    # NOISE_REL 倍，并以最大帧能量的 1/4 封顶，避免把有声段误当噪声。
    rms_sorted = sorted(rms_values)
    q25 = rms_sorted[max(0, len(rms_sorted) // 4 - 1)]
    rms_max = rms_sorted[-1] if rms_sorted else 0.0
    energy_gate = min(max(RMS_FLOOR, NOISE_REL * q25), 0.25 * rms_max)
    for r in records:
        r["energy_ok"] = r["rms"] >= energy_gate

    # ---- 第三阶段：时域中值滤波，抑制离群周期（含倍频程跳变）----
    # 窗长按时间尺度自适应：约覆盖 ±80 ms，至少 5 帧、取奇数。
    m = len(records)
    target = max(5, int(round(0.16 * rate / hop_size)))
    if target % 2 == 0:
        target += 1
    half_w = target // 2
    med_tau = [None] * m
    for i in range(m):
        lo, hi = max(0, i - half_w), min(m, i + half_w + 1)
        # 中值基于所有存在的周期候选；强周期帧权重更高（重复计入）
        window = []
        for j in range(lo, hi):
            tau_j = records[j]["tau"]
            if tau_j is None:
                continue
            window.append(tau_j)
            if records[j]["strong"]:
                window.append(tau_j)
        if window:
            med_tau[i] = _median(window)

    for i, r in enumerate(records):
        r["med_tau"] = med_tau[i]
        if r["tau"] is None or r["med_tau"] is None:
            continue
        ratio = r["tau"] / r["med_tau"]
        # 邻域中必须有足够多的帧支持中值解释，才做音轨级八度纠偏
        lo, hi = max(0, i - half_w), min(m, i + half_w + 1)
        support = sum(
            1 for j in range(lo, hi)
            if records[j]["tau"] is not None
            and abs(records[j]["tau"] - r["med_tau"]) / r["med_tau"] < 0.15
        )
        neighbor_count = sum(1 for j in range(lo, hi)
                             if records[j]["tau"] is not None)
        if neighbor_count >= 3 and support / neighbor_count >= 0.6:
            for k in (0.5, 2.0, 3.0):
                if abs(ratio - k) / k < 0.12:
                    r["tau"] = r["med_tau"]
                    r["dprime"] = min(r["dprime"], YIN_THRESHOLD)
                    r["conf"] = max(0.0, min(1.0, 1.0 - r["dprime"]))
                    r["strong"] = True
                    break

    # 音轨边缘复核：若邻域缺少中值支持，则向最近的强周期帧看齐，
    # 纠正段尾/段首被亚谐波拉偏（如 140 Hz -> 70 Hz）的帧。
    for i, r in enumerate(records):
        if r["tau"] is None:
            continue
        for step in (1, 2, 3):
            for j in (i - step, i + step):
                if 0 <= j < m and records[j]["strong"] and records[j]["tau"]:
                    ref_tau = records[j]["tau"]
                    ratio = r["tau"] / ref_tau
                    if any(abs(ratio - k) / k < 0.12 for k in (0.5, 2.0, 3.0)):
                        r["tau"] = ref_tau
                        r["dprime"] = min(r["dprime"], YIN_THRESHOLD)
                        r["conf"] = max(0.0, min(1.0, 1.0 - r["dprime"]))
                    break
            else:
                continue
            break

    # ---- 第四阶段：有声/无声判定 + 孤立段插补 ----
    # 轨迹可信帧：强帧，或与邻域中值一致的宽松/弱候选。弱候选必须挂靠
    # 在一条由多个可信帧组成的真实轨迹上，单帧噪声谷无法形成轨迹。
    credible = [False] * m
    cred_left = [0] * m
    cred_right = [0] * m
    for i, r in enumerate(records):
        if not r["energy_ok"] or r["tau"] is None:
            continue
        if r["strong"]:
            credible[i] = True
        elif r["med_tau"] is not None and r["conf"] >= 0.45:
            if abs(r["tau"] - r["med_tau"]) / r["med_tau"] < 0.15:
                credible[i] = True
    cnt = 0
    for i in range(m):
        cred_left[i] = cnt
        if credible[i]:
            cnt += 1
    cnt = 0
    for i in range(m - 1, -1, -1):
        cred_right[i] = cnt
        if credible[i]:
            cnt += 1

    for i, r in enumerate(records):
        backing = cred_left[i] + cred_right[i]
        if r["strong"] and r["energy_ok"]:
            r["voiced"] = True
        elif (r["energy_ok"] and credible[i] and backing >= 2
              and r["med_tau"] is not None):
            # 宽松/弱候选只在邻域存在真实可信轨迹时成立（滑音、低信噪比）
            r["voiced"] = True
        else:
            r["voiced"] = False

    # 音轨级剪枝：真实浊音产生连续多个周期一致的可信帧。任何最大连续
    # 有声段若可信帧数量不足 MIN_RUN_CREDIBLE，整段判为无声（消除起始
    # 静音里由噪声深谷诱发的虚假短轨迹与弱信号段虚假基频）。
    MIN_RUN_CREDIBLE = 3
    i = 0
    while i < m:
        if not records[i]["voiced"]:
            i += 1
            continue
        j = i
        while j < m and records[j]["voiced"]:
            j += 1
        cred_cnt = sum(1 for k in range(i, j) if credible[k])
        if cred_cnt < MIN_RUN_CREDIBLE:
            for k in range(i, j):
                records[k]["voiced"] = False
        i = j

    voiced_flag = [r["voiced"] for r in records]
    for i in range(m):
        if voiced_flag[i]:
            continue
        left = any(voiced_flag[max(0, i - CONTEXT):i])
        right = any(voiced_flag[i + 1:i + 1 + CONTEXT])
        r = records[i]
        # 两侧稳定有声、本帧能量充分且有周期候选时插补
        if left and right and r["energy_ok"] and r["tau"] is not None:
            r["voiced"] = True
            r["conf"] = min(r["conf"], 0.6)

    # ---- 第五阶段：轨迹内插值（弱信号/非平稳段的连续性）----
    GAP_FILL_MAX = max(CONTEXT, int(round(0.12 * rate / hop_size)))
    GAP_FILL_LONG = int(round(0.45 * rate / hop_size))
    ANCHOR_CONF = 0.65
    i = 0
    while i < m:
        if records[i]["voiced"]:
            i += 1
            continue
        j = i
        while j < m and not records[j]["voiced"]:
            j += 1
        gap_len = j - i
        # 首尾缺失绝不桥接，避免把起始/结尾静音里的噪声谷连成假轨迹
        bounded = 0 < i and j < m
        energetic = all(records[k]["energy_ok"] for k in range(i, j))
        if not bounded or not energetic:
            i = j
            continue
        anchor_l = records[i - 1]
        anchor_r = records[j]
        # 短间隙也要求两侧都是可信锚点，防止从噪声帧起桥
        short_ok = (gap_len <= GAP_FILL_MAX and credible[i - 1]
                    and credible[j])
        # 长间隙桥接的防误报条件：两端必须是高置信可信锚点、间隙内至少
        # 存在一定数量的周期候选（真正的长静音没有候选），能量全部充分。
        in_gap_candidates = sum(1 for k in range(i, j)
                                if records[k]["tau"] is not None)
        long_ok = (gap_len <= GAP_FILL_LONG
                   and credible[i - 1] and credible[j]
                   and anchor_l["conf"] >= ANCHOR_CONF
                   and anchor_r["conf"] >= ANCHOR_CONF
                   and in_gap_candidates >= max(1, gap_len // 3)
                   and anchor_l["tau"] is not None
                   and anchor_r["tau"] is not None)
        if short_ok or long_ok:
            # 锚点周期须相容（同一音轨：相差不超过 2 倍），避免跨段桥接
            if (anchor_l["tau"] is not None and anchor_r["tau"] is not None
                    and 0.5 < anchor_l["tau"] / anchor_r["tau"] < 2.0):
                for k in range(i, j):
                    w = (k - i + 1) / (gap_len + 1)
                    records[k]["tau"] = (1 - w) * anchor_l["tau"] + w * anchor_r["tau"]
                    records[k]["voiced"] = True
                    records[k]["conf"] = 0.5 if short_ok else 0.4
        i = j

    # ---- 第六阶段：有声轨迹内 τ 中值平滑（抑噪 + 剔除残余八度离群）----
    # 只在连续有声段内部做 5 点中值（短于 5 帧的段取可用全部帧）；
    # 无声边界不参与，滑音的单调趋势经中值后仅轻微平滑、不产生倍频程跳变。
    smoothed_tau = [r["tau"] for r in records]
    start = 0
    while start < m:
        if not records[start]["voiced"]:
            start += 1
            continue
        end = start
        while end < m and records[end]["voiced"]:
            end += 1
        for k in range(start, end):
            lo, hi = max(start, k - 2), min(end, k + 3)
            vals = [records[j]["tau"] for j in range(lo, hi)
                    if records[j]["tau"] is not None]
            if len(vals) >= 3:
                smoothed_tau[k] = _median(vals)
        start = end

    frames = []
    for r, tau_s in zip(records, smoothed_tau):
        if r["voiced"] and tau_s is not None:
            frames.append(dict(offset=r["offset"], voiced=True,
                               hz=rate / tau_s, confidence=r["conf"]))
        else:
            # 无声帧置信度固定 0，弱周期证据保留在内部记录中供审计
            frames.append(dict(offset=r["offset"], voiced=False,
                               hz=None, confidence=0.0))

    frames, stats = _stability(frames, rate, frame_size, hop_size)
    stats["energy_gate"] = energy_gate
    stats["noise_rms_q25"] = q25
    digest = _digest(frames, stats)
    return dict(frames=frames, stats=stats, digest=digest)


def _stability(frames, rate, frame_size, hop_size):
    """相邻有声帧稳定性统计。"""
    voiced_idx = [i for i, f in enumerate(frames) if f["voiced"]]
    voiced_count = len(voiced_idx)
    unvoiced_count = len(frames) - voiced_count

    hz_seq = [frames[i]["hz"] for i in voiced_idx]
    med_hz = _median(hz_seq)
    conf_seq = [frames[i]["confidence"] for i in voiced_idx]

    # 相邻有声帧（统计直接相邻的有声帧）
    jumps = 0
    octave_jumps = 0
    for a, b in zip(voiced_idx, voiced_idx[1:]):
        if b - a == 1:
            ratio = frames[b]["hz"] / frames[a]["hz"]
            if ratio >= 1.9 or ratio <= 1.0 / 1.9:
                octave_jumps += 1
            if ratio >= 1.12 or ratio <= 1.0 / 1.12:
                jumps += 1

    # 半音偏差（相对中位数），度量整体稳定性
    cent_dev = []
    if med_hz > 0:
        for hz in hz_seq:
            cent_dev.append(abs(1200.0 * math.log2(hz / med_hz)))
    cent_median = _median(cent_dev)

    stats = {
        "voiced": voiced_count,
        "unvoiced": unvoiced_count,
        "voiced_ratio": (voiced_count / len(frames)) if frames else 0.0,
        "median_hz": med_hz,
        "median_confidence": _median(conf_seq),
        "median_cent_deviation": cent_median,
        "adjacent_jump_count": jumps,
        "octave_jump_count": octave_jumps,
        "frame_size": frame_size,
        "hop_size": hop_size,
        "sample_rate": rate,
    }
    return frames, stats


def _median(seq):
    if not seq:
        return 0.0
    s = sorted(seq)
    k = len(s)
    mid = k // 2
    if k % 2:
        return float(s[mid])
    return 0.5 * (s[mid - 1] + s[mid])


def _digest(frames, stats):
    """与帧长无关的归一化摘要：帧用窗口中心时间量化后参与哈希。"""
    hs = stats["hop_size"]
    fs = stats["frame_size"]
    compact = [
        {
            "t": round((f["offset"] + (fs - 1) / 2.0) / hs, 3),
            "v": int(f["voiced"]),
            "hz": None if f["hz"] is None else round(f["hz"], 2),
            "c": round(f["confidence"], 3),
        }
        for f in frames
    ]
    payload = json.dumps(
        {"frames": compact,
         "summary": {k: v for k, v in stats.items()
                     if k not in ("frame_size", "hop_size", "sample_rate")}},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


if __name__ == "__main__":
    data = json.loads(Path(sys.argv[1]).read_text())
    result = analyze(data["samples"], data["sample_rate"],
                     data["frame_size"], data["hop_size"])
    Path(sys.argv[2]).write_text(json.dumps(result))

