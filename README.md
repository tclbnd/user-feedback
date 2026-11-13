# 本地 RTP/H.264 重组

Python 3.9+。输入 JSON 包含 window（1–64）、first_sequence（期望首包 16 位序号）、packets（到达顺序）。包为 {sequence,timestamp,marker,hex}，timestamp 为 32 位 RTP 时间，marker 为布尔，hex 为 payload。输入为单流非交错模式。第一个扩展时间戳为初始 32 位原值，后续按序在相邻差小于半周期的前提下扩展。以 first_sequence 为起始连续序号，允许乱序与重复。乱序窗口达到上限或 EOF 时结算缺包。不得无限累积挂起包。统计 max_pending、lost、duplicates、late、malformed，可加丢弃明细。

支持单 NAL、STAP-A（16 位大端长度）、FU-A。验证空载荷、非法类型、STAP-A 长度、FU 的 S/E、R、时间戳与类型一致。以时间戳、marker 和连续序号组织访问单元；若某帧有缺包、缺起止或错误载荷，整帧不输出。发生损坏后丢弃无法安全依赖旧状态的帧。此离线捕获约定每个恢复用 IDR 访问单元在其 IDR VCL 之前包含完整 SPS 和 PPS，只有该单元可恢复输出；已经恢复后遇到完整的非 IDR 可正常继续。首帧也从此种 IDR 启动。

执行 `python3 depacketize.py packets.json recovered.h264 report.json`。按访问单元顺序写 Annex B，每个 NAL 用四字节起始码。报告 units 的项为 {timestamp,nals}，其中 timestamp 是扩展时间戳，nals 是对应 NAL 字节 SHA-256 列表，顺序与 Annex B 一致。source.h264 是用本地 FFmpeg 生成的 12 帧源素材，含访问单元分隔符和周期 IDR。make_demo.py 将其打成带回绕的本地包日志。

## 实现要点

- 序号扩展以已接受的最高扩展序号为参照做 ±半周期对齐；窗口封口规则为新到包 `ext - next_expected >= window` 时把落后缺槽结算为丢包，EOF 时同样结算，保证挂起队列不超过 window。已结算槽位的晚到包若在最近 2×window 个已处理序号内记为 duplicate，否则记为 late。
- 时间戳在结算后的协议顺序上扩展：首包取 32 位原值，后续按相邻差小于 2^31 选择周期。
- STAP-A 逐项校验 16 位大端长度与内部 NAL 类型；FU-A 校验 S/E 互斥、R 位、片段类型以及续片的时间戳/类型一致性。载荷错误计入 malformed 并使当前访问单元不可信。
- 访问单元以时间戳分组、marker 闭合；缺包、缺 marker、FU 截断或载荷错误都会导致整帧丢弃并记录 drops 原因。任何丢包/错误都会清除参考状态，只有完整且带匹配 SPS/PPS（位于首个 VCL 之前）的 IDR 访问单元能恢复输出。

## 验证

`python3 make_demo.py` 生成无丢包夹具（跨 16 位序号与 32 位时间戳回绕，注入重复与乱序）；`python3 make_demo.py --faults -o fault_packets.json` 额外丢失第 1 帧 FU-A 中片与第 4 帧 IDR 的 SPS/PPS STAP-A。`python3 verify.py` 运行两条验收：逐 NAL 哈希与扩展时间戳对真值、故障下无残片输出且恢复段（第 8–11 帧）与源逐帧一致（有 ffmpeg 时用其解码比对，否则以 Annex B 字节一致作为解码等价判定），并断言 max_pending 不超过窗口。
