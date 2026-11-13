# Matroska 截断索引重建

Python 3.9+。执行 `python3 repair_mkv.py clipped.mkv restored.mkv report.json`。demo.mkv 为真实 H.264/Opus 容器，包含短 Cluster 和关键帧。可用任意字节截断测试。需要按 EBML element ID 和长度读 Segment、Info、Tracks、Cluster。处理固定或未知 Cluster 长度，短尾部只丢弃不完整 Block，不用虚构补齐。保留完整 SimpleBlock 或 BlockGroup 的字节、时刻与轨道。轨道及 CodecPrivate 完整，不能依赖源端 Cues 存在。处理指定 Xiph 和 fixed lacing，保留样本边界，不必转码。

输出修复容器必须含有效 SeekHead 和视频 CuePoint，SeekPosition/CueClusterPosition 相对输出 Segment payload 开头。关键帧 CueTime 为 Cluster Timestamp 加有符号 Block 相对值。可以重写 Cluster 容器长度并保留原有完整元素。源端 SeekHead/Cues 可能指向已不存在数据，应替换。缺 Info、Tracks 或无合法 EBML 报错并定位字节位置。

report 至少含 recovered、discarded。recovered 项可含原始偏移、轨道、时间与数据摘要；discarded 报告短读、错误范围或无法解析的尾部。不要求任何第三方 Python 模块。独立验收读取原始和输出容器并用 FFmpeg seek 解码。
