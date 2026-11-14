#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多特征联合静音判定的长录音智能剪辑
====================================
用法: python3 cut.py req.json out.json
req.json:
{
  "pcm": "in.wav",                 # 相对 req.json 所在目录的 16bit 单声道 PCM wav
  "fs": 16000,                     # 可选, 仅做校验
  "features": {
    "level_db": -45,               # 电平门限(dBFS), 与自适应噪声底取严者
    "flat": 0.6,                   # 谱平坦度门限(>判为无结构噪声)
    "min_ms": 200,                 # 静音候选最小持续
    "min_content_ms": 150,         # 保留段最小长度(短于视为瞬态, 删除)
    "merge_ms": 300,               # 保留段间隔小于该值则合并
    "guard_ms": 50,                # 剪切点前后保护
    "fade_ms": 20,                 # 淡入淡出
    "breath_ms": 80,               # 气口时长上界(更短的静音不剪)
    "block_ms": 2000               # 分块大小(仅影响 IO, 不影响判定)
  },
  "human": [{"start": 1.0, "end": 2.0}],   # 人工剪辑参考(可选, 由评测脚本使用)
  "render": "out_render.wav"               # 可选, 渲染保留段并加淡入淡出
}

输出 out.json:
{
  "keep": [{"start": s, "end": e}],       # 最终保留段(含保护, 秒)
  "drop": [{"start": s, "end": e}],       # 最终删除段(秒)
  "boundaries": [ms, ...],                # 可复核分界点(保护后实际剪切点, ms)
  "raw_boundaries": [ms, ...],            # 保护前的内容/静音原始切换点(ms)
  "segments": [ 逐段决策, 含判据贡献 ],
  "frames":   [ 可选(debug=true)逐帧特征与判定 ],
  "stats":    { 全局统计、阈值自适应、门限贡献占比 },
  "config":   { 实际生效参数 }
}

设计要点
--------
1. 多特征联合判定(20ms 帧):
   - 电平 rms_db: 相对满量程; 门限 = max(配置 level_db, 自适应噪声底+6dB),
     噪声底取全片 rms 的低分位, 空调底噪整体抬高时自动跟随, 不会被当成内容;
   - 谱平坦度 flatness = geomean(|X|)/mean(|X|): 宽带噪声(空调/气流)接近 1,
     语音/低语呈谐波结构明显偏低; 低语即使电平低也不判静音;
   - 过零率 zcr: 低语高 zcr 附加保护;
   - 峰值因子 crest: 瞬态(键盘敲击) crest 高且能量快速衰变, 与持续稳态噪声区分;
   - 持续性: 静音候选须连续 >= min_ms, 气口(<breath_ms)不剪; 保留段须 >=
     min_content_ms, 孤立敲击短段作为瞬态删除(突发噪声不被保留为内容)。
2. 剪切点处理: 最小段长、guard 前后保护、fade 淡入淡出、merge 近邻保留段合并。
3. 分块无关: StreamFramer 只按固定帧长切样本, 所有状态基于帧序号, 块大小仅
   决定每次喂入样本数; 逐帧特征与分段结果在任意 block_ms 下逐位一致。
"""
import json
import math
import os
import struct
import sys
import wave

# ---------------------------------------------------------------- 常量/默认
DEFAULTS = {
    "level_db": -45.0,
    "flat": 0.6,
    "min_ms": 200.0,
    "min_content_ms": 150.0,
    "merge_ms": 300.0,
    "guard_ms": 50.0,
    "fade_ms": 20.0,
    "breath_ms": 80.0,
    "block_ms": 2000.0,
    "hysteresis_db": 6.0,     # 退出静音需高出进入门限的迟滞量
    "noise_margin_db": 6.0,   # 自适应门限相对噪声底的余量
    "noise_pct": 10.0,        # 噪声底取 rms 分位
    "zcr_whisper": 0.30,      # 高过零率低语保护
    "whisper_floor_db": -56.0,  # 结构优先路径的绝对低语电平下沿
}
FRAME_MS = 20.0
EPS = 1e-12
_TWIDDLE = {}


# ---------------------------------------------------------------- FFT(纯库 radix-2)
def _twiddle(n):
    tw = _TWIDDLE.get(n)
    if tw is None:
        ang = -2.0 * math.pi / n
        tw = [(math.cos(ang * k), math.sin(ang * k)) for k in range(n >> 1)]
        _TWIDDLE[n] = tw
    return tw


def fft(re, im):
    """原地迭代基2 FFT, 长度必须为 2 的幂。"""
    n = len(re)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            re[i], re[j] = re[j], re[i]
            im[i], im[j] = im[j], im[i]
    length = 2
    while length <= n:
        half = length >> 1
        tw = _twiddle(length)  # W_length^k, k=0..half-1
        for i in range(0, n, length):
            for k in range(half):
                a = i + k
                b = a + half
                wr, wi = tw[k]
                tr = wr * re[b] - wi * im[b]
                ti = wr * im[b] + wi * re[b]
                re[b] = re[a] - tr
                im[b] = im[a] - ti
                re[a] += tr
                im[a] += ti
        length <<= 1


# ---------------------------------------------------------------- 流式分帧
class StreamFramer:
    """块大小无关的分帧器: 内部仅维护帧起始位置与余数缓冲。"""

    def __init__(self, frame_n):
        self.frame_n = frame_n
        self.buf = []
        self.pos = 0  # 已产出帧的起始样本下标

    def push(self, samples):
        self.buf.extend(samples)
        out = []
        while len(self.buf) >= self.frame_n:
            frame = self.buf[: self.frame_n]
            del self.buf[: self.frame_n]
            start = self.pos
            self.pos += self.frame_n
            out.append((start, frame))
        return out

    def flush(self):
        if not self.buf:
            return []
        frame = self.buf + [0] * (self.frame_n - len(self.buf))
        start = self.pos
        self.pos += self.frame_n
        self.buf = []
        return [(start, frame)]


# ---------------------------------------------------------------- 逐帧特征
def _quantile(sorted_vals, q):
    if not sorted_vals:
        return -120.0
    idx = q * (len(sorted_vals) - 1)
    lo = int(math.floor(idx))
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def frame_features(frame, fs, fft_n, window):
    n = len(frame)
    # 电平: RMS 与峰值
    s2 = 0
    peak = 0
    zc = 0
    prev = frame[0]
    for v in frame:
        s2 += v * v
        a = v if v >= 0 else -v
        if a > peak:
            peak = a
        if (prev >= 0) != (v >= 0) and (prev != 0 or v != 0):
            zc += 1
        prev = v
    rms = math.sqrt(s2 / n)
    rms_db = 20.0 * math.log10(max(rms, 1.0) / 32768.0)
    peak_db = 20.0 * math.log10(max(peak, 1) / 32768.0)
    crest = peak / max(rms, 1.0)
    zcr = zc / n
    # 谱平坦度(加 Hann 窗, 零填充到 fft_n)
    re = [frame[i] * window[i] for i in range(n)] + [0.0] * (fft_n - n)
    im = [0.0] * fft_n
    fft(re, im)
    n_bins = fft_n // 2
    log_sum = 0.0
    mag_sum = 0.0
    used = 0
    # 仅用 80Hz~fs/2 的有效频段, 去掉直流与极低频漂移
    k0 = max(1, int(math.ceil(80.0 * fft_n / fs)))
    for k in range(k0, n_bins):
        mag = math.sqrt(re[k] * re[k] + im[k] * im[k]) + EPS
        log_sum += math.log(mag)
        mag_sum += mag
        used += 1
    flatness = math.exp(log_sum / used) / (mag_sum / used) if used else 1.0
    return {
        "rms_db": rms_db,
        "peak_db": peak_db,
        "flat": flatness,
        "zcr": zcr,
        "crest": crest,
    }


# ---------------------------------------------------------------- 状态机判定
def rolling_quantile(values, q):
    """values 为当前窗口内 rms_db, 返回其 q 分位(局部噪声底估计)。"""
    s = sorted(values)
    return _quantile(s, q)


def classify_frames(frames, cfg):
    """每帧局部噪声底跟踪 + 多特征状态机。

    局部噪声底: 1.2s 滑动窗内 rms 的低分位(最小统计量), 对空调这类持续稳态
    噪声可跟随到其自身电平, 从而把自适应门限抬到空调之上; 语音窗内仍由安静
    帧决定底噪。门限 = max(配置 level_db, 局部底噪 + margin)。
    结构优先: 平坦度明显低于门限且高于绝对低语底噪的帧直接判内容, 使电平
    低于固定门限的低语不被删除。
    """
    win = max(8, int(round(1200.0 / FRAME_MS)))  # 1.2s 窗口
    flat_thr = cfg["flat"]
    zcr_whisper = cfg["zcr_whisper"]
    abs_floor_db = cfg.get("whisper_floor_db", -52.0)

    rms_global = sorted(f["rms_db"] for f in frames)
    global_floor = _quantile(rms_global, cfg["noise_pct"] / 100.0)

    labels = []
    for idx, f in enumerate(frames):
        a = max(0, idx - win + 1)
        local_floor = rolling_quantile(
            [g["rms_db"] for g in frames[a:idx + 1]], cfg["noise_pct"] / 100.0)
        enter_db = max(cfg["level_db"], local_floor + cfg["noise_margin_db"])
        exit_db = enter_db + cfg["hysteresis_db"]
        f["local_noise_db"] = local_floor
        f["gate_enter_db"] = enter_db
        f["gate_exit_db"] = exit_db
        f["noise_floor_db"] = global_floor

        structured = f["flat"] < flat_thr                     # 共振峰/谐波结构
        whisper_like = (f["flat"] < flat_thr + 0.08
                        and f["zcr"] >= zcr_whisper)          # 高过零率低语
        # 结构优先: 电平低于门限但有明确结构的低语帧(裕度 0.02 覆盖调制谷值)
        structure_first = (f["rms_db"] >= abs_floor_db
                           and (f["flat"] < flat_thr + 0.02 or whisper_like))
        # 高电平 + 结构: 正常语音
        level_voice = f["rms_db"] >= enter_db and (structured or whisper_like)
        labels.append(1 if (structure_first or level_voice) else 0)
    # 3 帧多数表决, 消除单帧抖动(真实边界偏移 <= 1 帧=20ms)
    voted = labels[:]
    for i in range(1, len(labels) - 1):
        s = labels[i - 1] + labels[i] + labels[i + 1]
        voted[i] = 1 if s >= 2 else 0
    return voted, global_floor, \
        max(f["gate_enter_db"] for f in frames), \
        max(f["gate_exit_db"] for f in frames)


def smooth_labels(labels, frames, cfg, frame_ms):
    """持续性约束:
       - 静音段短于 min_ms(且 <= breath_ms 视为气口) -> 回退为内容;
       - 内容段短于 min_content_ms 且呈瞬态特征(高峰值因子/衰变) -> 回退为静音。
    """
    lab = labels[:]
    min_sil_frames = max(1, int(round(cfg["min_ms"] / frame_ms)))
    breath_frames = max(1, int(round(cfg["breath_ms"] / frame_ms)))
    min_content_frames = max(1, int(round(cfg["min_content_ms"] / frame_ms)))
    zcr_whisper = cfg["zcr_whisper"]

    def runs(seq):
        out = []
        i = 0
        while i < len(seq):
            j = i
            while j < len(seq) and seq[j] == seq[i]:
                j += 1
            out.append((seq[i], i, j))
            i = j
        return out

    # 1) 短静音不剪: <= breath_ms 的气口无条件保留; breath_ms~min_ms 之间的
    #    抖动静音也因未达 min_ms 最小持续而回退为内容
    for val, i, j in runs(lab):
        dur = j - i
        if val == 0 and (dur <= breath_frames or dur < min_sil_frames):
            for k in range(i, j):
                lab[k] = 1
    # 2) 孤立短内容: 瞬态(敲击)删除, 但真正的低语(结构+高zcr)保留
    for val, i, j in runs(lab):
        if val == 1 and (j - i) < min_content_frames:
            seg = frames[i:j]
            crest = sum(s["crest"] for s in seg) / len(seg)
            zcr = sum(s["zcr"] for s in seg) / len(seg)
            flat = sum(s["flat"] for s in seg) / len(seg)
            peak_db = max(s["peak_db"] for s in seg)
            rms_db = sum(s["rms_db"] for s in seg) / len(seg)
            transient = (crest >= 5.0) or ((peak_db - rms_db) >= 12.0)
            whisper_like = (flat < cfg["flat"]) and (zcr >= zcr_whisper)
            if transient and not whisper_like:
                for k in range(i, j):
                    lab[k] = 0
    return lab


def labels_to_runs(lab):
    out = []
    i = 0
    while i < len(lab):
        j = i
        while j < len(lab) and lab[j] == lab[i]:
            j += 1
        out.append((lab[i], i, j))
        i = j
    return out


# ---------------------------------------------------------------- 分段/合并/保护
def build_segments(lab, frames, fs, frame_n, cfg):
    """生成原始段、合并近邻保留段、施加保护, 返回 keep/drop/segments/边界。"""
    frame_ms = frame_n / fs * 1000.0
    merge_frames = max(1, int(round(cfg["merge_ms"] / frame_ms)))
    guard_frames = max(0, int(round(cfg["guard_ms"] / frame_ms)))
    total = len(lab)

    # --- 合并: 间隔小于 merge_frames 的两个内容段之间的静音并入内容
    merged = lab[:]
    runs = labels_to_runs(merged)
    for val, i, j in runs:
        if val == 0 and 0 < i and j < total and (j - i) < merge_frames:
            for k in range(i, j):
                merged[k] = 1

    raw_runs = labels_to_runs(merged)
    raw_segments = []
    for val, i, j in raw_runs:
        seg_frames = frames[i:j]
        raw_segments.append({
            "kind": "keep" if val == 1 else "drop",
            "f_start": i,
            "f_end": j,
            "start": i * frame_n / fs,
            "end": j * frame_n / fs,
            "duration_ms": (j - i) * frame_ms,
            "evidence": _evidence(seg_frames, val, cfg),
        })

    # --- 保护: 每个保留段向两侧扩 guard(落在删除段内的部分被收回为保留)
    keep_idx = [0] * total
    for val, i, j in raw_runs:
        if val == 1:
            a = max(0, i - guard_frames)
            b = min(total, j + guard_frames)
            for k in range(a, b):
                keep_idx[k] = 1
    final_runs = labels_to_runs(keep_idx)

    keep, drop = [], []
    for val, i, j in final_runs:
        item = {"start": round(i * frame_n / fs, 4),
                "end": round(j * frame_n / fs, 4)}
        (keep if val == 1 else drop).append(item)

    raw_boundaries = sorted(
        {round(j * frame_n / fs * 1000.0, 1) for _, _, j in raw_runs[:-1]}
    )
    boundaries = sorted(
        {round(j * frame_n / fs * 1000.0, 1) for _, _, j in final_runs[:-1]}
    )
    return keep, drop, raw_segments, raw_boundaries, boundaries, merged, frame_ms


def _evidence(seg_frames, val, cfg):
    """逐段判据贡献: 各特征均值 + 各判据投票占比 + 触发原因。"""
    m = len(seg_frames) or 1
    rms = sum(f["rms_db"] for f in seg_frames) / m
    peak = sum(f["peak_db"] for f in seg_frames) / m
    flat = sum(f["flat"] for f in seg_frames) / m
    zcr = sum(f["zcr"] for f in seg_frames) / m
    crest = sum(f["crest"] for f in seg_frames) / m
    enter = seg_frames[0]["gate_enter_db"] if seg_frames else cfg["level_db"]
    v_level = sum(1 for f in seg_frames if f["rms_db"] >= enter) / m
    v_struct = sum(1 for f in seg_frames if f["flat"] < cfg["flat"]) / m
    v_zcr = sum(1 for f in seg_frames if f["zcr"] >= cfg["zcr_whisper"]) / m
    reasons = []
    if val == 1:
        if v_level >= 0.5:
            reasons.append("电平高于自适应门限(%.1fdBFS)" % enter)
        if v_struct >= 0.5:
            reasons.append("谱平坦度%.2f低于%.2f呈结构化(语音/低语)" % (flat, cfg["flat"]))
        if v_zcr >= 0.5:
            reasons.append("高过零率%.2f低语保护" % zcr)
    else:
        if v_level < 0.5:
            lf = seg_frames[0]["local_noise_db"] if seg_frames else -120
            reasons.append("电平低于自适应门限(%.1fdBFS, 局部噪声底%.1fdBFS)" % (enter, lf))
        if v_struct < 0.5:
            reasons.append("谱平坦度%.2f高于%.2f呈宽带稳态噪声(空调/气流)" % (flat, cfg["flat"]))
        if crest >= 5.0:
            reasons.append("高峰值因子%.1f瞬态衰变(键盘/敲击)" % crest)
        if not reasons:
            reasons.append("持续性静音候选达到min_ms")
    return {
        "rms_db": round(rms, 2), "peak_db": round(peak, 2),
        "flatness": round(flat, 3), "zcr": round(zcr, 3),
        "crest": round(crest, 2),
        "gate_contrib": {"level_keep_ratio": round(v_level, 3),
                         "structure_keep_ratio": round(v_struct, 3),
                         "whisper_zcr_ratio": round(v_zcr, 3)},
        "reasons": reasons,
    }


# ---------------------------------------------------------------- 渲染
def render_wav(pcm_path, keep, fade_ms, out_path):
    w = wave.open(pcm_path, "rb")
    nch, sw, wfs, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    raw = w.readframes(n)
    w.close()
    total = n * nch
    samples = list(struct.unpack("<%dh" % total, raw))
    fade = int(round(fade_ms / 1000.0 * wfs))

    def apply(piece, fade_in=True, fade_out=True):
        L = len(piece)
        fn = min(fade, L // 2)
        for k in range(fn):
            g = (k + 1) / (fn + 1)
            if fade_in:
                piece[k] = int(piece[k] * g)
            if fade_out:
                piece[L - 1 - k] = int(piece[L - 1 - k] * g)
        return piece

    out = []
    for seg in keep:
        a = max(0, int(seg["start"] * wfs)) * nch
        b = min(n, int(seg["end"] * wfs)) * nch
        out.extend(apply(samples[a:b]))
    with wave.open(out_path, "wb") as wo:
        wo.setnchannels(nch)
        wo.setsampwidth(sw)
        wo.setframerate(wfs)
        wo.writeframes(struct.pack("<%dh" % len(out), *out))


# ---------------------------------------------------------------- 主流程
def run(req_path, out_path):
    req = json.load(open(req_path))
    base = os.path.dirname(req_path)
    cfg = dict(DEFAULTS)
    cfg.update(req.get("features") or {})
    pcm_path = os.path.join(base, req["pcm"])
    debug = bool(req.get("debug"))

    w = wave.open(pcm_path, "rb")
    nch, sw, fs, nframes = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    if nch != 1 or sw != 2:
        raise ValueError("仅支持 16bit 单声道 PCM wav, 得到 nch=%d sw=%d" % (nch, sw))
    if "fs" in req and req["fs"] != fs:
        raise ValueError("采样率校验失败: req=%d wav=%d" % (req["fs"], fs))
    frame_n = int(round(fs * FRAME_MS / 1000.0))
    fft_n = 1
    while fft_n < frame_n:
        fft_n <<= 1
    window = [0.5 - 0.5 * math.cos(2 * math.pi * i / max(1, frame_n - 1))
              for i in range(frame_n)]
    block_n = int(round(fs * cfg["block_ms"] / 1000.0))

    framer = StreamFramer(frame_n)
    frames = []
    f_index = 0

    def consume(chunk):
        nonlocal f_index
        for start, frame in framer.push(chunk):
            feats = frame_features(frame, fs, fft_n, window)
            feats["frame"] = f_index
            feats["t_ms"] = round(start / fs * 1000.0, 2)
            frames.append(feats)
            f_index += 1

    while True:
        raw = w.readframes(block_n)
        if not raw:
            break
        chunk = list(struct.unpack("<%dh" % (len(raw) // 2), raw))
        consume(chunk)
    w.close()
    for start, frame in framer.flush():
        feats = frame_features(frame, fs, fft_n, window)
        feats["frame"] = f_index
        feats["t_ms"] = round(start / fs * 1000.0, 2)
        frames.append(feats)
        f_index += 1

    if not frames:
        raise ValueError("空音频")

    labels, global_floor, _mx1, _mx2 = classify_frames(frames, cfg)
    lab = smooth_labels(labels, frames, cfg, FRAME_MS)
    keep, drop, raw_segments, raw_boundaries, boundaries, merged, _ = build_segments(
        lab, frames, fs, frame_n, cfg)

    for seg in keep:
        seg["fade_in_ms"] = cfg["fade_ms"]
        seg["fade_out_ms"] = cfg["fade_ms"]

    total_ms = nframes / fs * 1000.0
    keep_ms = sum(s["end"] - s["start"] for s in keep) * 1000.0
    # 自适应门限按帧变化, 汇总典型值供复核
    enter_vals = sorted(f["gate_enter_db"] for f in frames)
    exit_vals = sorted(f["gate_exit_db"] for f in frames)
    enter_med = enter_vals[len(enter_vals) // 2]
    exit_med = exit_vals[len(exit_vals) // 2]
    stats = {
        "duration_ms": round(total_ms, 2),
        "keep_ms": round(keep_ms, 2),
        "drop_ms": round(total_ms - keep_ms, 2),
        "keep_ratio": round(keep_ms / total_ms, 4),
        "n_keep": len(keep),
        "n_drop": len(drop),
        "global_noise_floor_db": round(global_floor, 2),
        "local_gate_enter_median_db": round(enter_med, 2),
        "local_gate_enter_max_db": round(enter_vals[-1], 2),
        "local_gate_exit_median_db": round(exit_med, 2),
        "configured_level_db": cfg["level_db"],
        "gate_source": "adaptive_local_floor" if enter_med > cfg["level_db"]
                       else "configured_level",
        "frame_ms": FRAME_MS,
        "fft_n": fft_n,
        "block_ms_effective_io_only": cfg["block_ms"],
        "criteria_votes": {
            "level_pass_frames": sum(1 for f in frames if f["rms_db"] >= f["gate_enter_db"]),
            "structure_pass_frames": sum(1 for f in frames if f["flat"] < cfg["flat"]),
            "whisper_zcr_frames": sum(1 for f in frames if f["zcr"] >= cfg["zcr_whisper"]),
            "structure_first_frames": sum(
                1 for f in frames
                if f["rms_db"] >= cfg["whisper_floor_db"]
                and (f["flat"] < cfg["flat"]
                     or (f["flat"] < cfg["flat"] + 0.12
                         and f["zcr"] >= cfg["zcr_whisper"]))),
            "total_frames": len(frames),
        },
    }
    result = {
        "keep": keep,
        "drop": drop,
        "boundaries": boundaries,
        "raw_boundaries": raw_boundaries,
        "segments": raw_segments,
        "stats": stats,
        "config": {k: cfg[k] for k in sorted(cfg)},
    }
    if debug:
        result["frames"] = [
            {k: (round(v, 4) if isinstance(v, float) else v)
             for k, v in f.items() if k in ("frame", "t_ms", "rms_db", "peak_db",
                                            "flat", "zcr", "crest")}
            for f in frames
        ]
        result["frame_labels"] = merged
    with open(out_path, "w") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    if req.get("render"):
        render_wav(pcm_path, keep, cfg["fade_ms"], os.path.join(base, req["render"]))
    return result


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    run(sys.argv[1], sys.argv[2])
