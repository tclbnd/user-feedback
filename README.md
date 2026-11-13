# 本地 RTP/H.264 重组

Python 3.9+。输入 JSON 包含 window（1–64）、first_sequence（期望首包 16 位序号）、packets（到达顺序）。包为 {sequence,timestamp,marker,hex}，timestamp 为 32 位 RTP 时间，marker 为布尔，hex 为 payload。输入为单流非交错模式。第一个扩展时间戳为初始 32 位原值，后续按序在相邻差小于半周期的前提下扩展。以 first_sequence 为起始连续序号，允许乱序与重复。乱序窗口达到上限或 EOF 时结算缺包。不得无限累积挂起包。统计 max_pending、lost、duplicates、late、malformed，可加丢弃明细。

支持单 NAL、STAP-A（16 位大端长度）、FU-A。验证空载荷、非法类型、STAP-A 长度、FU 的 S/E、R、时间戳与类型一致。以时间戳、marker 和连续序号组织访问单元；若某帧有缺包、缺起止或错误载荷，整帧不输出。发生损坏后丢弃无法安全依赖旧状态的帧。此离线捕获约定每个恢复用 IDR 访问单元在其 IDR VCL 之前包含完整 SPS 和 PPS，只有该单元可恢复输出；已经恢复后遇到完整的非 IDR 可正常继续。首帧也从此种 IDR 启动。

执行 `python3 depacketize.py packets.json recovered.h264 report.json`。按访问单元顺序写 Annex B，每个 NAL 用四字节起始码。报告 units 的项为 {timestamp,nals}，其中 timestamp 是扩展时间戳，nals 是对应 NAL 字节 SHA-256 列表，顺序与 Annex B 一致。source.h264 是用本地 FFmpeg 生成的 12 帧源素材，含访问单元分隔符和周期 IDR。make_demo.py 将其打成带回绕的本地包日志。
