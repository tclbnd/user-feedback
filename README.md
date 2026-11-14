# 谱图相位迭代重建（仅幅度谱 → 相位）

纯 Python 标准库实现（无 numpy/scipy 等第三方依赖），自带 radix-2 FFT、
STFT/iSTFT 与 Griffin-Lim 家族迭代算法，完全确定性、可复现、可断点续跑。

## 运行

```bash
python3 demo.py               # 6 类素材端到端，产出 outputs/ 全部交付物
python3 test_acceptance.py    # 19 项验收测试（应全部 PASS）
python3 phase.py req.json output.json   # 单任务 CLI（向后兼容旧接口）
```

`req.json`：
```json
{"mag": [[帧][频点]实信号单边幅度谱], "win": 128, "hop": 112,
 "n": 信号长度, "iters": 800, "seed": 7, "tol": 1e-3,
 "method": "afgl", "parallel": false, "outdir": "可选输出目录",
 "checkpoint": "可选，来自上次输出的检查点（用于续跑）"}
```
- 每帧频点数 = `win // 2 + 1`（含直流与奈奎斯特）。
- `method`：`gl`（基础 Griffin-Lim，误差严格单调）、`fgl`（固定动量快速 GL）、
  `afgl`（自适应动量快速 GL，默认，对打击乐/宽频噪声收敛最快最稳）。
- `hop` 默认 `7/8*win`（12.5% 重叠）。重叠越低，幅度-only 投影约束集相切越缓、
  收敛平台越低；显式传 `hop` 可使用 25%/50%/75% 重叠。

## 算法

两次投影交替，误差为归一化 Frobenius 谱一致性误差
`e = ||C(S) - Pm(C(S))||_F / ||Mag||_F`：

1. **时域重叠一致** `C = STFT∘iSTFT`：逆变换回时域、重叠相加、再正变换。
2. **幅度约束** `Pm`：`Z ← Mag · exp(j·angle(Z))`，用复数归一化实现，与三角式逐位等价。

关键工程点：
- 中心帧对齐 + **零填充**，保证 `C` 是严格幂等的正交投影（实测 `||C(S)-C^2(S)||~1e-15`），
  这是 Griffin-Lim 误差单调下降的前提；早期反射填充版本会产生零频点并破坏单调性。
- 单位归一化 + sqrt-Hann 分析/合成窗 + WOLA，内部样本机器精度完美重建（>300 dB）。
  Hann 窗端点严格为 0，信号首/尾各 1 个样本在谱中数学上不可观测，属 STFT 固有死区，
  不影响任何谱帧与一致性误差。
- `afgl` 在 `C(S)` 上做动量外推 `Z + alpha(Z-Z_prev)`，误差下降则 alpha 缓增至 0.95、
  回升则 x0.7 回退，确定性调度，解决普通 GL 在打击乐/噪声处 ~1/k 慢收敛与平台问题。
- 非整数频率（off-bin）泄漏由分析窗与重叠自然处理，无需特殊分支。

## 可复现与续跑

- 固定 `seed` 的 `random.Random` 生成初始随机相位；FFT 与所有累加均确定性，
  `parallel=true/false` 仅切换帧调度路径、浮点结合顺序完全相同，结果逐位一致。
- 检查点保存当前谱、上一轮 `C(S)`、动量 alpha、上一步误差、迭代计数与 RNG 状态；
  分两段/三段续跑与一次性运行的信号、谱、逐次误差**逐样本相同**。

## SNR 的正确解读

幅度-only 相位问题对噪声/打击乐等信号不存在唯一波形解：
- **谱幅度 SNR 约 320 dB**（机器精度），说明 GL 优化的幅度约束被精确满足；
- **波形 SNR 为负**，因为估计相位与真实相位无关（实测相关系数约 0.04），
  这是问题本身的信息缺失，而非实现缺陷；对照实验中使用真实相位（oracle）
  波形 SNR 可达 200+ dB。结构化的语音/短促音波形相关性相对更高。

## 交付物（outputs/）

每类素材目录含 `phase.json`（相位谱）、`reconstructed.wav`（8kHz/16-bit）、
`err.json`（逐次误差）、`convergence.svg`；根目录另有 6 类对比曲线
`convergence_all.svg`、4 种子 `reproducibility.svg`、`summary.json`、
`checkpoint_demo.json`。

## 文件

- `phase.py`：FFT、STFT/iSTFT、GL/FGL/AFGL、检查点、WAV/SVG 输出、CLI。
- `signals.py`：6 类确定性素材（语音/打击乐/噪声/静音/混合/短促音）。
- `demo.py`：端到端演示与交付物生成。
- `test_acceptance.py`：19 项验收测试。
