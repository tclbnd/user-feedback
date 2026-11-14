# 多特征静音判定智能剪辑

纯 Python 标准库实现（无 numpy/scipy 依赖，内置 radix-2 FFT）。

## 用法

```
python3 cut.py req.json output.json
```

`req.json`：

```json
{
  "pcm": "in.wav",
  "fs": 16000,
  "features": {
    "level_db": -45,
    "flat": 0.6,
    "min_ms": 200,
    "min_content_ms": 150,
    "merge_ms": 300,
    "guard_ms": 50,
    "fade_ms": 20,
    "breath_ms": 80,
    "block_ms": 2000
  },
  "human": [{"start": 1.0, "end": 2.0}],
  "render": "out_render.wav",
  "debug": false
}
```

仅支持 16bit 单声道 PCM wav。`human` 为可选人工参考，`render` 可选（渲染保留段并加淡入淡出），
`debug=true` 额外输出逐帧特征与帧标签。

## 判定原理（多特征联合）

20ms 帧、512 点 Hann 窗 FFT，每帧提取：

| 特征 | 含义 | 作用 |
| --- | --- | --- |
| `rms_db` / `peak_db` | 电平平抑与峰值 | 门限判定、峰值因子 |
| `flat` 谱平坦度 | geomean(|X|)/mean(|X|)，80Hz~Nyquist | 宽带噪声(空调≈0.7) vs 结构化语音/低语(≤0.6) |
| `zcr` 过零率 | 波形符号翻转密度 | 高过零率低语附加保护 |
| `crest` 峰值因子 | peak/RMS | 瞬态(键盘敲击 crest≥5、能量衰变)识别 |

- **自适应门限**：1.2s 滑动窗内 RMS 低分位作为局部噪声底（最小统计量），
  进入门限 = max(配置 `level_db`, 局部噪声底 + 6dB)，退出再 +6dB 迟滞。
  空调底噪整体抬高时门限自动跟随，不会把空调当内容。
- **结构优先**：电平低于门限但谱平坦度明显偏低（共振峰结构）或高过零率的帧判为内容，
  使低于固定门限的低语不被删除（绝对下沿 `whisper_floor_db=-56dBFS`）。
- **持续性约束**：静音候选须连续 ≥ `min_ms`，短于 `breath_ms` 的气口不剪；
  保留段短于 `min_content_ms` 且呈瞬态特征（高峰值因子/衰变）判为敲击并删除，
  突发噪声不会被保留为内容。
- **后处理**：间隔 < `merge_ms` 的保留段合并；每个剪切点前后 `guard_ms` 保护；
  渲染时 `fade_ms` 淡入淡出；首尾保护自动钳制不越界。

## 输出

```json
{
  "keep": [{"start": s, "end": e, "fade_in_ms": 20, "fade_out_ms": 20}],
  "drop": [{"start": s, "end": e}],
  "boundaries": [ms],
  "raw_boundaries": [ms],
  "segments": [{"kind": "keep|drop", "start": 0, "end": 0, "duration_ms": 0,
                "evidence": {"rms_db": 0, "flatness": 0, "zcr": 0, "crest": 0,
                             "gate_contrib": {"level_keep_ratio": 0,
                                              "structure_keep_ratio": 0,
                                              "whisper_zcr_ratio": 0},
                             "reasons": ["判据贡献中文说明"]}}],
  "stats": {"adaptive...": "门限来源与各判据投票帧数"},
  "config": {"实际生效参数"}
}
```

`boundaries` 为含保护的实际剪切点，`raw_boundaries` 为保护前原始内容/静音切换点，均为可复核毫秒时间戳。

## 分块无关性

`StreamFramer` 只按固定帧长切样本，全部状态基于帧序号；`block_ms` 仅决定每次读盘样本数。
评测以 7 种块大小（23/77/137/1000/2000/5000/12345ms，含非整除帧长的质数块）复跑，
`keep/drop/boundaries/raw_boundaries` 逐位一致。

## 验收复现

```
python3 run_all.py
```

| 脚本 | 职责 |
| --- | --- |
| `tests_check.py` | FFT 对朴素 DFT 自检、各信号类特征探针 |
| `make_fixtures.py` | 合成 28s 综合场景与 12s 注入专项（空调/键盘/低语/气口） |
| `evaluate.py` | 人工指标、逐段决策一致性、分块不变性 |
| `compare_baseline.py` | 与纯电平门限基线 `cut_baseline.py` 对照 |
| `test_edge.py` | 全静音/极短/非整除长度/立体声拒绝/确定性 |
| `test_params.py` | 瞬态删除、最小段长、保护带、近邻合并、淡入淡出 |

验收阈值（见 `data/evaluation.json`）：边界中位误差 ≤80ms、误删语音 ≤2%、误留静音 ≤5%；
注入样例逐段决策与期望一致；两种以上分块大小边界摘要一致。
实测：边界中位误差 40ms、误删语音 0%/0.4%、误留静音 3.6%/1.7%，全部通过。

## 缺陷基线

`cut_baseline.py` 为原始纯电平门限实现（保留备查）。同批样例上其误留静音 66%~82%
（空调 −38dBFS 高于 −45 门限被当内容）、注入场景误删语音 8.7%（低语 −48dBFS 低于门限），
对照说明多特征联合判定修复的问题。

