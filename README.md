# 流式 PCM 采样率转换

Python 3.9+。运行 `python3 resample.py demo.json output.json`。输入为 in_rate、out_rate、channels（1–8），以及 commands 列表。push 的 frames 是按采样时刻交错的通道数组。finish 刷新尾部，再次 finish 返回空。reset 放弃上一段未刷新的尾部并清零时间；reset 后新段从采样 0 开始。无效 push 返回该项 error，不消耗任何输入。finish 后非空 push 需要先 reset。输出沿用每条命令的 frames 和诊断。

P/Q 是 out_rate/in_rate 最简分数。每段第 m 个输出的输入坐标 t=mQ/P，输出总数 ceil(NP/Q)。线性相位滤波器的计算延迟只影响何时返回，输出索引已经补偿延迟。左右超出输入部分视为零。推流中某个输出的最右非零支撑未到齐时等待，finish 后做零填充。空块不改变状态。

给定连续核，R=80、beta=8.6、fc=0.45×min(1,P/Q)，在 |d|≤R 时 h(d)=2fc×sinc(2fc×d)×I0(beta×sqrt(1−(d/R)^2))/I0(beta)，其余为零，sinc(x)=sin(pi×x)/(pi×x)。输出为各整数输入索引 i 的 sample[i]×h(i−t) 之和，不额外归一化。按有理相位复用系数，保留未来输出仍需的输入窗口。每条结果的 buffered_frames 应报告命令返回后实际保留的源帧数。可增补分数相位、累计输出和输入计数诊断。可用 Python 标准库实现 I0 级数。

提供全序列参考入口，说明与主流式路径的调用方法。程序只处理数值 PCM 帧，命令格式允许直接检查采样点及时间轴，无需解码依赖。
