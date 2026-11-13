# 流式 PCM 采样率转换

Python 3.9+。运行 `python3 resample.py demo.json output.json`。输入为 in_rate、out_rate、channels（1–8），以及 commands 列表。push 的 frames 是按采样时刻交错的通道数组。finish 刷新尾部，再次 finish 返回空。reset 放弃上一段未刷新的尾部并清零时间；reset 后新段从采样 0 开始。无效 push 返回该项 error，不消耗任何输入。finish 后非空 push 需要先 reset。输出沿用每条命令的 frames 和诊断。

P/Q 是 out_rate/in_rate 最简分数。每段第 m 个输出的输入坐标 t=mQ/P，输出总数 ceil(NP/Q)。线性相位滤波器的计算延迟只影响何时返回，输出索引已经补偿延迟。左右超出输入部分视为零。推流中某个输出的最右非零支撑未到齐时等待，finish 后做零填充。空块不改变状态。

给定连续核，R=80、beta=8.6、fc=0.45×min(1,P/Q)，在 |d|≤R 时 h(d)=2fc×sinc(2fc×d)×I0(beta×sqrt(1−(d/R)^2))/I0(beta)，其余为零，sinc(x)=sin(pi×x)/(pi×x)。输出为各整数输入索引 i 的 sample[i]×h(i−t) 之和，不额外归一化。按有理相位复用系数，保留未来输出仍需的输入窗口。每条结果的 buffered_frames 应报告命令返回后实际保留的源帧数。可增补分数相位、累计输出和输入计数诊断。可用 Python 标准库实现 I0 级数。

提供全序列参考入口，说明与主流式路径的调用方法。程序只处理数值 PCM 帧，命令格式允许直接检查采样点及时间轴，无需解码依赖。

## 状态与断点

`Converter` 在 `push` 之间保留三样东西：有理相位（由 `next_output_index` 推出的
`phase_numerator/phase_denominator`）、输入历史窗口（只保留 `floor(t)−R` 之后仍要用到的
源帧，实测上界约为 `R + P/Q` 帧），以及当前段的累计计数。因此同一组源帧无论被切成
整块、单帧还是随机块，输出的长度、相位和逐样本值都不变；块长为 1 也走同一套滤波计算，
不额外归一化、不做块内局部时钟。

显式不连续有三种等价写法，均结束旧段并从源采样 0 开始新段，旧段信号不会泄入新段：

* `Converter.push(frames, discontinuity=True)` —— 先补零刷新旧段尾部，再推入新块；
* `Converter.mark_discontinuity(flush=True)` —— 只做边界，返回旧段尾部；`flush=False`
  则丢弃未刷新的尾部；
* JSON 命令 `{"op": "push", ..., "discontinuity": true}` 或
  `{"op": "discontinuity", "flush": true, "frames": [...]}`。

`finish` 之后的非空 `push` 报错，需先 `reset`；空块（含 `[]`）是纯空操作，不改变相位、
不改变计数。任一命令失败只影响该命令，后续命令可照常继续，诊断也仍然有效。

`status()` 的关键字段：`buffered_frames`（返回后实际保留的源帧数，即队列长度）、
`next_output_index`（本段下一个输出序号 m）、`phase_numerator/phase_denominator`、
`input_count`/`output_count`（构造以来的累计值）、`segment_index`、`finished`。

## 全序列参考路径

`reference_resample(in_rate, out_rate, channels, frames)`（别名 `reference`）不做任何
流式状态，直接对整段源帧做连续核卷积，输出个数为 `ceil(NP/Q)`，段外视为零。它既可在
测试里单独调用，也可用于对照：

```python
import resample
streamed = resample.Converter(48000, 44100, 2)
blocks = [...]                              # 任意切分
frames = [f for b in blocks for f in streamed.push(b)] + streamed.finish()
control = resample.reference_resample(48000, 44100, 2, whole_source)
```

命令行同样提供对照运行：`python3 resample.py --reference in.json out_ref.json` 会用
`reference_replay` 处理同一份 JSON（每个段结束时整段转换），逐命令累加计数与主流式
路径一致，拼接后的帧序列与主流式结果在 2e-6 内一致（本实现实测 <1e-12）。

## 测试

`python3 -m unittest discover -s tests`。覆盖整块/单帧/固定种子随机分块的长度与相位
一致性（最大误差 ≤ 2e-6）、直接卷积验证首尾脉冲位置、下采样通带误差 ≤ 0.2 dB 与
折叠带抑制 ≥ 65 dB、断点前后信号隔离、错误返回后状态可用。
