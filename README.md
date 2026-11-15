# 逐帧基频跟踪

运行 `python3 pitch.py input.json output.json`。输入字段为 `sample_rate`（Hz）、`frame_size`、`hop_size`、`samples`（单声道浮点采样）。采样从索引 0 开始，每次移动 `hop_size`，只输出具备 `frame_size` 个真实采样的完整帧（不补零）。基频工作范围为 70–600 Hz。对同一段音频，改变帧长后用各自窗口中心 `offset+(frame_size-1)/2` 对应原始时间。

## 算法（纯 Python 标准库，无第三方依赖）

1. **YIN 差分函数与 CMND**：`difference_function` 由有偏自相关推导 d(τ)，`cumulative_mean_normalized` 计算累积均值归一化差分 d′(τ)（d′(0)=1）。主阈值 0.15，另设 0.35 宽松阈值覆盖滑音等非平稳但仍周期的帧。
2. **八度纠偏**：`refine_period` 优先采纳“首个低于阈值的谷”（低周期解释，抑制强二次谐波导致的倍频误报），同时保留 2τ/3τ 亚谐波谷证据；仅当亚谐波谷本身过硬阈值且与主谷深度相容（≤1.7 倍）才降周期。后续音轨级中值/边缘复核再消除残余倍频程跳变。每帧置信度为 `1-d′(τ*)`（抛物线插值后），取值 0–1。
3. **有声/无声判定**：绝对 RMS 门限 + 基于低四分位噪声底的自适应能量门限；弱候选必须挂靠在由 ≥3 个周期一致可信帧组成的轨迹上，短/长间隙插值有强锚点与候选数约束，弱信号段与纯静音不产生虚假基频。

## 输出

- `frames`：每帧 `offset`、`voiced`、`hz`（无声为 null）、`confidence`（无声为 0）。
- `stats`：`voiced`/`unvoiced` 计数、`voiced_ratio`、`median_hz`、`median_confidence`、`median_cent_deviation`（相对中位基频的半音偏差）、`adjacent_jump_count`、`octave_jump_count`、能量门限等。
- `digest`：基于窗口中心时间量化帧与归一化摘要的 SHA-256，与帧长选择无关，便于跨帧长/复跑比对。

## 验收复跑

- `python3 validate.py`：2 组帧长（2048/4096）× 4 组信噪比（40/20/10/3 dB）的带标注合成信号（稳态音、110→240→130 Hz 滑音、强二次谐波、低基频、纯噪声无声段），输出中位误差、漏判/误判率、倍频程跳变与帧长一致性。
- `python3 test_octave.py`：八度纠偏与亚谐波证据专项。
- `python3 test_cli.py`：CLI 契约、完整帧/中心对齐等边缘帧规则、确定性与弱信号鲁棒性。

当前结果：稳态基频中位误差 ≤ 0.33%（阈值 0.5%），无声误判 0%（阈值 5%），滑音 0 次倍频程跳变，两种帧长在全部信噪比下判定摘要一致。
