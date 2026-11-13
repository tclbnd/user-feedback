# 广播响度归一化

Python 3.9+ 纯标准库实现，无第三方依赖。运行
`python3 normalize.py input.wav output.wav report.json --layout stereo`。
输入输出为 16-bit 线性 PCM WAV（采样率任意，窗长按采样率自适应）。

## 实现（ITU-R BS.1770-4）

- **K 加权**：标准两级双二阶（高频搁架 + 高通），转置直接 II 型，状态跨块保留。
- **块级统计**：400 ms 窗、100 ms 步长，逐样本推进并在精确块边界快照加权均方，
  因此 `--block`（I/O 读取帧数）取任意正整数都得到逐位一致的结果。
- **双门控**：先施加 −70 LUFS 绝对门，再按绝对门均值计算 −10 LU 相对门，
  对最终保留块求积分响度；门控块数与相对阈值写入报告诊断。
- **声道权重与 LFE**：mono/stereo 权重 1.0；5.1（FL、FR、FC、LFE、BL、BR）
  环绕声权重 1.41，LFE 权重 0——不参与响度但计入真峰值。
- **真峰值**：8 倍过采样 Kaiser(β=9.2) 加窗 sinc 多相内插，逐样本维护状态，
  对全部声道（含 LFE）取最大幅度；针对**落盘后的 int16 PCM** 复测。
- **短节目 / 全静音 / 未知布局**：不足一个完整 400 ms 窗报
  `insufficient_duration` 且 `lufs=null`；无块通过绝对门报 `silence`；
  未知布局或声道数与文件不符直接报错退出。

## 两遍处理与优先级

1. 第一遍流式测量积分响度与输入真峰值；
2. 由目标响度算目标增益，由真峰值上限算峰值增益，取两者较小值——
   **峰值优先**：无法同时满足时保证真峰值不超限，输出响度允许偏离目标，
   并在 `diagnostics.warnings` 回报具体限制原因；
3. 第二遍施加整体增益、半进位量化落盘，再对实际 PCM 复测响度与真峰值；
   若量化/采样间峰值仍超限，按实测余量降增益重试（最多 4 次）。

`--target` 默认 −23 LUFS，`--ceiling` 默认 −1 dBTP。

## 报告

报告至少包含 `{layout, reason, gain_db, input:{lufs,dbtp}, output:{lufs,dbtp}, reason}`，
`reason` 为 `target`、`peak_limited`、`silence` 或 `insufficient_duration`；
无定义数值用 `null`，JSON 以 `allow_nan=False` 写出（无 NaN/Infinity）。
`diagnostics` 额外给出输入/输出文件路径（可对照到音频文件）、采样率、声道数、
时长、`--block` 实参、门控块统计、LFE 排除标志与全部告警。

## 验收与工具

- `python3 make_demo.py out.wav --case {normal,silence,limited,short,intersample,surround}`
  生成长静音、短节目、5.1、采样间峰值等测试向量；
- `python3 ref_meter.py file.wav --layout stereo` 为**独立参考计量器**
  （整段切片分块、直接 I 型滤波、Hann 窗 8 倍过采样，实现路径与生产端不同），
  `python3 ref_meter.py --selftest` 运行 BS.1770 锚点自检；
- `python3 test_acceptance.py` 运行全部验收：与参考计量器积分响度差 ≤ 0.2 LU、
  真峰值差 ≤ 0.3 dB；可行节目 −23 LUFS ±0.3 LU、输出 ≤ −1 dBTP；
  限峰/全静音诊断、无 NaN、`--block` 不变性与错误路径。

