# 逐帧基频跟踪

运行 `python3 pitch.py input.json output.json`。输入字段为 `sample_rate`（Hz）、`frame_size`、`hop_size`、`samples`（单声道浮点采样）。采样从索引 0 开始，每次移动 `hop_size`，只输出具备 `frame_size` 个真实采样的完整帧。基频工作范围为 70–600 Hz。对同一段音频，改变帧长后用各自窗口中心对应原始时间。

输出 `frames`、`stats`、`digest`。每帧含 `offset`（帧首采样索引）、`voiced`、`hz`（无声时为 null）、`confidence`（0–1）；`stats` 至少含 `voiced`、`unvoiced` 及相邻有声帧的稳定性指标。基线按过零率估计，弱信号和强二次谐波会误报。
