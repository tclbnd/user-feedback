"""ITU-R BS.1770 响度归一化器（纯标准库实现）。

流程：
  第一遍：流式读取（块长仅影响 I/O 分块），K 加权 + 400ms/100ms 块统计、
          绝对门(-70 LUFS)与相对门(-10 LU)积分响度，以及 8 倍过采样真峰值。
  第二遍：按积分响度求整体增益，再用真峰值上限约束增益（峰值优先），
          量化落盘后对实际 PCM 复测真峰值与输出响度，必要时降增益重试。
"""
import argparse
from array import array
import json
import math
from pathlib import Path
import wave

# ---------------------------------------------------------------- 布局与权重
# BS.1770-4 表 1 声道权重；LFE 权重为 0（不参与响度，但参与真峰值）
LAYOUTS = {
    "mono":   {"channels": 1, "weights": (1.0,)},
    "stereo": {"channels": 2, "weights": (1.0, 1.0)},
    "5.1":    {"channels": 6,
               "weights": (1.0, 1.0, 1.0, 0.0, 1.41, 1.41)},  # FL FR FC LFE BL BR
}
ABS_GATE_LUFS = -70.0
REL_GATE_OFFSET_LU = -10.0
BLOCK_S = 0.4
HOP_S = 0.1


class NormalizeError(Exception):
    """可向用户报告的明确错误。"""


# ---------------------------------------------------------------- K 加权滤波
def k_weighting_coeffs(fs):
    """返回 BS.1770-4 规定的两级双二阶系数 (b0,b1,b2,a1,a2)，a0 归一为 1。"""
    # 第一级：高频搁架
    f0, gain_db, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / fs)
    vh = 10.0 ** (gain_db / 20.0)
    vb = vh ** 0.4996667821008259
    a0 = 1.0 + k / q + k * k
    shelf = (
        (vh + vb * k / q + k * k) / a0,
        2.0 * (k * k - vh) / a0,
        (vh - vb * k / q + k * k) / a0,
        2.0 * (k * k - 1.0) / a0,
        (1.0 - k / q + k * k) / a0,
    )
    # 第二级：高通
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / fs)
    a0 = 1.0 + k / q + k * k
    highpass = (
        1.0 / a0,
        -2.0 / a0,
        1.0 / a0,
        2.0 * (k * k - 1.0) / a0,
        (1.0 - k / q + k * k) / a0,
    )
    return shelf, highpass


class Biquad:
    """转置直接 II 型双二阶，状态可跨块保留。"""

    __slots__ = ("b0", "b1", "b2", "a1", "a2", "z1", "z2")

    def __init__(self, coeffs):
        self.b0, self.b1, self.b2, self.a1, self.a2 = coeffs
        self.z1 = 0.0
        self.z2 = 0.0

    def process(self, x):
        y = self.b0 * x + self.z1
        self.z1 = self.b1 * x - self.a1 * y + self.z2
        self.z2 = self.b2 * x - self.a2 * y
        return y


# ---------------------------------------------------------------- 块级响度计
class BlockLoudnessMeter:
    """流式块响度：滤波器与环形缓冲状态跨读取块持续，结果与 --block 无关。"""

    def __init__(self, nch, weights, fs):
        self.nch = nch
        self.weights = weights
        self.win = int(round(BLOCK_S * fs))
        self.hop = int(round(HOP_S * fs))
        shelf, highpass = k_weighting_coeffs(fs)
        self.filters = [[Biquad(shelf), Biquad(highpass)] for _ in range(nch)]
        self.rings = [array("d", [0.0]) * self.win for _ in range(nch)]
        self.sums = [0.0] * nch
        self.count = 0
        self.block_ms = []          # 每个门控块的加权均方值（线性）
        self._blocks_emitted = 0    # 已产出块数，保证与读取分块方式无关

    def process(self, channels):
        """channels: 每声道一个长度相同的数值序列。逐样本推进并在精确块边界快照。"""
        n = len(channels[0])
        win, hop = self.win, self.hop
        for j in range(n):
            pos = self.count + j
            for c in range(self.nch):
                f1, f2 = self.filters[c]
                y = f2.process(f1.process(float(channels[c][j])))
                y2 = y * y
                ring = self.rings[c]
                idx = pos % win
                self.sums[c] += y2 - ring[idx]
                ring[idx] = y2
            # 窗满后，每 hop 个样本在边界处产出一个块（与读取分块无关）
            if pos >= win - 1 and (pos + 1 - win) % hop == 0:
                m = 0.0
                for c in range(self.nch):
                    m += self.weights[c] * (self.sums[c] / win)
                self.block_ms.append(m)
                self._blocks_emitted += 1
        self.count += n

    def integrated(self):
        """返回 (lufs 或 None, 门控诊断 dict)。"""
        diag = {"blocks_total": len(self.block_ms),
                "blocks_absolute_gated": 0, "blocks_gated": 0,
                "relative_threshold_lufs": None}
        if not self.block_ms:
            return None, diag                       # 不足一个完整 400ms 窗
        abs_floor = 10.0 ** ((ABS_GATE_LUFS + 0.691) / 10.0)
        abs_gated = [m for m in self.block_ms if m > abs_floor]
        diag["blocks_absolute_gated"] = len(abs_gated)
        if not abs_gated:
            return None, diag                       # 全静音（无块过绝对门）
        mean_abs = sum(abs_gated) / len(abs_gated)
        l_kg = -0.691 + 10.0 * math.log10(mean_abs)
        rel_thresh = l_kg + REL_GATE_OFFSET_LU
        rel_floor = 10.0 ** ((rel_thresh + 0.691) / 10.0)
        gated = [m for m in abs_gated if m > rel_floor]
        diag["blocks_gated"] = len(gated)
        diag["relative_threshold_lufs"] = rel_thresh
        lufs = -0.691 + 10.0 * math.log10(sum(gated) / len(gated))
        return lufs, diag


# ---------------------------------------------------------------- 真峰值计
def _bessel_i0(x):
    total = 1.0
    term = 1.0
    for m in range(1, 32):
        term *= (x * x) / (4.0 * m * m)
        total += term
        if term < 1e-18 * total:
            break
    return total


def polyphase_filters(upsample, taps_per_phase, beta):
    """Kaiser 加窗 sinc 原型，拆成 upsample 个多相分支。"""
    length = upsample * taps_per_phase
    center = (length - 1) / 2.0
    proto = array("d", [0.0]) * length
    den = _bessel_i0(beta)
    for k in range(length):
        d = k - center
        if d == 0.0:
            sinc = 1.0
        else:
            sinc = math.sin(math.pi * d / upsample) / (math.pi * d)
        u = 2.0 * d / (length - 1)
        window = _bessel_i0(beta * math.sqrt(max(0.0, 1.0 - u * u))) / den
        proto[k] = sinc * window
    # 归一化：使各多相分支在直流下增益为 1（用第 0 分支增益作比例）
    gain0 = sum(proto[r * upsample] for r in range(taps_per_phase))
    scale = 1.0 / gain0
    phases = []
    for p in range(upsample):
        phases.append(array("d", (proto[p + r * upsample] * scale
                                  for r in range(taps_per_phase))))
    return phases


class TruePeakMeter:
    """8 倍过采样 Kaiser-sinc 多相内插真峰值，状态跨块保留，LFE 等全部声道计入。"""

    def __init__(self, nch, upsample=8, taps_per_phase=24, beta=9.2):
        self.phases = polyphase_filters(upsample, taps_per_phase, beta)
        self.upsample = upsample
        self.taps = taps_per_phase
        self.states = [array("d", [0.0]) * taps_per_phase for _ in range(nch)]
        self.peak = 0.0

    def process(self, channels):
        nph = self.upsample
        nt = self.taps
        for c, samples in enumerate(channels):
            state = self.states[c]
            peak = self.peak
            for x in samples:
                # 移位：state[0]=当前样本，state[r]=x[m-r]
                for r in range(nt - 1, 0, -1):
                    state[r] = state[r - 1]
                state[0] = float(x)
                for h in self.phases:
                    acc = 0.0
                    for r in range(nt):
                        acc += h[r] * state[r]
                    a = abs(acc)
                    if a > peak:
                        peak = a
            self.peak = peak

    def dbtp(self):
        if self.peak <= 0.0:
            return None
        return 20.0 * math.log10(self.peak)


# ---------------------------------------------------------------- WAV 流式 I/O
def open_wav(path):
    w = wave.open(str(path), "rb")
    if w.getcomptype() != "NONE":
        raise NormalizeError("不支持压缩 WAV：" + str(path))
    if w.getsampwidth() != 2:
        raise NormalizeError(
            "仅支持 16-bit PCM，当前位宽 " + str(w.getsampwidth() * 8)
            + " bit：" + str(path))
    return w


def iter_chunks(w, block_frames, nch):
    """按 block_frames 读取，产出每声道 array('h')，块长不影响计量状态。"""
    while True:
        raw = w.readframes(block_frames)
        if not raw:
            return
        frames = array("h")
        frames.frombytes(raw)
        yield [frames[c::nch] for c in range(nch)]


def quantize(v):
    """浮点域(-1..1)半进位四舍五入到 int16 并饱和。"""
    scaled = v * 32768.0
    if scaled >= 0.0:
        q = int(math.floor(scaled + 0.5))
        return min(q, 32767)
    q = int(math.ceil(scaled - 0.5))
    return max(q, -32768)


# ---------------------------------------------------------------- 主流程
def normalize(source, target, layout, goal, ceiling, block_frames):
    if layout not in LAYOUTS:
        raise NormalizeError(
            "未知声道布局 '" + layout + "'，支持："
            + ", ".join(sorted(LAYOUTS)))
    cfg = LAYOUTS[layout]
    nch, weights = cfg["channels"], cfg["weights"]

    with open_wav(source) as w:
        params = w.getparams()
        if w.getnchannels() != nch:
            raise NormalizeError(
                "布局 " + layout + " 需要 " + str(nch) + " 声道，文件 "
                + str(source) + " 为 " + str(w.getnchannels()) + " 声道")
        fs = w.getframerate()
        total_frames = w.getnframes()
        duration = total_frames / fs
        # ---- 第一遍：测量输入 ----
        lmeter = BlockLoudnessMeter(nch, weights, fs)
        tpmeter = TruePeakMeter(nch)
        for chs in iter_chunks(w, block_frames, nch):
            normed = [array("d", (x / 32768.0 for x in col)) for col in chs]
            lmeter.process(normed)
            tpmeter.process(normed)

    input_lufs, gate_diag = lmeter.integrated()
    input_tp = tpmeter.dbtp()
    warnings = []

    # ---- 决定增益与原因（峰值优先于响度目标）----
    if input_lufs is None and not lmeter.block_ms:
        reason = "insufficient_duration"
        gain = 1.0
        warnings.append(
            "时长 %.3fs 不足一个 %.1fs 完整窗，无法定义积分响度，保持单位增益"
            % (duration, BLOCK_S))
    elif input_lufs is None:
        reason = "silence"
        gain = 1.0
        warnings.append("全部块低于 -70 LUFS 绝对门，判定为全静音，保持单位增益")
    else:
        target_gain = 10.0 ** ((goal - input_lufs) / 20.0)
        if input_tp is None:
            peak_gain = target_gain
        else:
            peak_gain = 10.0 ** ((ceiling - input_tp) / 20.0)
        if target_gain <= peak_gain:
            reason = "target"
            gain = target_gain
        else:
            reason = "peak_limited"
            gain = peak_gain
            warnings.append(
                "达到目标 %.0f LUFS 需增益 %+.2f dB，但真峰值上限 %g dBTP 仅允许 "
                "%+.2f dB；按峰值优先约束增益，输出响度将偏离目标"
                % (goal, 20 * math.log10(target_gain), ceiling,
                   20 * math.log10(peak_gain)))

    # ---- 第二遍：施加增益、量化落盘，并对落盘 PCM 复测 ----
    out_lufs = None
    out_tp = None
    for attempt in range(4):
        with open_wav(source) as src, wave.open(str(target), "wb") as dst:
            dst.setparams(params)
            out_lm = BlockLoudnessMeter(nch, weights, fs)
            out_tp_meter = TruePeakMeter(nch)
            for chs in iter_chunks(src, block_frames, nch):
                n = len(chs[0])
                processed = [
                    array("h", (quantize((x / 32768.0) * gain) for x in chs[c]))
                    for c in range(nch)
                ]
                flat = array("h", [0]) * (n * nch)
                for c in range(nch):
                    flat[c::nch] = processed[c]
                dst.writeframes(flat.tobytes())
                normed = [array("d", (q / 32768.0 for q in col))
                          for col in processed]
                out_lm.process(normed)
                out_tp_meter.process(normed)
        out_lufs, _ = out_lm.integrated()
        out_tp = out_tp_meter.dbtp()
        if (out_tp is None or out_tp <= ceiling
                or reason in ("silence", "insufficient_duration")):
            break
        # 量化/采样间峰值仍超限：按实测余量降低增益后重试
        excess = out_tp - ceiling
        gain *= 10.0 ** (-(excess + 0.05) / 20.0)
        warnings.append(
            "落盘复测真峰值 %.2f dBTP 超限，降低增益 %.2f dB 后重试"
            % (out_tp, excess + 0.05))
        reason = "peak_limited"
    else:
        warnings.append("多次重试后真峰值仍未满足上限，请检查输入")

    if not math.isfinite(gain) or gain <= 0:
        raise NormalizeError("内部错误：增益非有限值")

    return {
        "layout": layout,
        "reason": reason,
        "gain_db": 20.0 * math.log10(gain),
        "input": {"lufs": input_lufs, "dbtp": input_tp},
        "output": {"lufs": out_lufs, "dbtp": out_tp},
        "diagnostics": {
            "input_file": str(source),
            "output_file": str(target),
            "sample_rate": fs,
            "channels": nch,
            "duration_s": duration,
            "block_frames_arg": block_frames,
            "target_lufs": goal,
            "ceiling_dbtp": ceiling,
            "lfe_excluded_from_loudness": layout == "5.1",
            "gating": gate_diag,
            "warnings": warnings,
        },
    }


def main():
    p = argparse.ArgumentParser(description="BS.1770 响度归一化器")
    p.add_argument("input", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("report", type=Path)
    p.add_argument("--layout", default="stereo")
    p.add_argument("--target", type=float, default=-23.0)
    p.add_argument("--ceiling", type=float, default=-1.0)
    p.add_argument("--block", type=int, default=4096,
                    help="读取帧数（不影响测量结果）")
    a = p.parse_args()
    try:
        if a.block <= 0:
            raise NormalizeError("--block 必须为正整数")
        result = normalize(a.input, a.output, a.layout,
                           a.target, a.ceiling, a.block)
    except NormalizeError as e:
        raise SystemExit("error: " + str(e))
    a.report.write_text(json.dumps(result, sort_keys=True,
                                   indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
