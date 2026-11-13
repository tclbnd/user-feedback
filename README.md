# 本地 RTP/H.264 重组

Python 3.9+。输入 JSON 包含 window（1–64）、first_sequence（期望首包 16 位序号）、packets（到达顺序）。包为 {sequence,timestamp,marker,hex}，timestamp 为 32 位 RTP 时间，marker 为布尔，hex 为 payload。输入为单流非交错模式。第一个扩展时间戳为初始 32 位原值，后续按序在相邻差小于半周期的前提下扩展。以 first_sequence 为起始连续序号，允许乱序与重复。乱序窗口达到上限或 EOF 时结算缺包。不得无限累积挂起包。统计 max_pending、lost、duplicates、late、malformed，可加丢弃明细。

支持单 NAL、STAP-A（16 位大端长度）、FU-A。验证空载荷、非法类型、STAP-A 长度、FU 的 S/E、R、时间戳与类型一致。以时间戳、marker 和连续序号组织访问单元；若某帧有缺包、缺起止或错误载荷，整帧不输出。发生损坏后丢弃无法安全依赖旧状态的帧。此离线捕获约定每个恢复用 IDR 访问单元在其 IDR VCL 之前包含完整 SPS 和 PPS，只有该单元可恢复输出；已经恢复后遇到完整的非 IDR 可正常继续。首帧也从此种 IDR 启动。

执行 `python3 depacketize.py packets.json recovered.h264 report.json`。按访问单元顺序写 Annex B，每个 NAL 用四字节起始码。报告字段：

- `units`：项为 {timestamp,nals}，timestamp 是扩展时间戳，nals 是对应 NAL 字节 SHA-256 列表，顺序与 Annex B 一致。
- `dropped`：被丢弃访问单元的 {timestamp,reason} 明细（缺包、缺 marker、载荷错误、参考状态不可信等）。
- `stats`：{max_pending,lost,duplicates,late,malformed}。窗口封口规则：到达包超出 next_expected+window 时，结算窗口之前的缺包；EOF 结算剩余全部缺包。pending 队列恒不超过 window。

source.h264 是用本地 FFmpeg 生成的 12 帧源素材，含访问单元分隔符和周期 IDR。make_demo.py 将其打成带回绕的本地包日志，并生成生成器真值 truth.json；`--faults fu-middle,stap` 分别注入 FU-A 中片丢失与 IDR 参数集（STAP-A）丢失。

验收：`python3 verify.py` 构建无损（跨 16 位序号与 32 位时间戳回绕、含重复与乱序）、FU-A 中片丢失、参数集丢失及合成迟到/重复/错误包夹具，逐项比对真值、统计与丢弃明细，并用独立的码流前端解析恢复出的每帧（若 PATH 中存在 ffmpeg 则追加逐帧像素比对）。
