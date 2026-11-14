# Matroska 截断索引重建

在文件尾被截断、目录索引（SeekHead/Cues）丢失的情况下重建 MKV/WebM
容器：保留所有仍完整的 Block，重建 SeekHead 与视频关键帧 Cues，并输出
已恢复/已丢弃数据清单。纯标准库实现，不依赖第三方 Python 模块，也不需要
ffmpeg / mkvtoolnix。

## 用法

```bash
python3 repair_mkv.py clipped.mkv restored.mkv report.json
```

退出码：`0` 修复成功；`1` 因缺少关键元数据拒绝修复（错误信息带字节偏移）；
`2` 源文件无法读取。

`demo.mkv` 为真实 H.264/Opus 容器（含短 Cluster、SimpleBlock 与一个
BlockGroup），可用任意字节截断测试。

## 实现要点

* **EBML 基础**（`mkvrecover/ebml.py`）：区分元素 ID 与长度 VINT（ID 保留
  长度标记位），支持未知大小长度；`parse_block` 解析 SimpleBlock/Block 的
  轨道号、有符号相对时间、关键帧标志，以及 None / Xiph / fixed / EBML 四种
  lacing，逐帧返回边界与内容。
* **段级扫描**（`mkvrecover/repair.py`）：按父子边界遍历 Segment 直接子元素。
  固定大小元素超出可用字节即判为截断；未知大小 Cluster 通过“逐个子元素
  边界推进、遇到非 Cluster 子元素 ID 即认定为下一个兄弟”定位终点，避免在
  Block 载荷中误命中。
* **块校验与保留**：每个 Block 都重新解析；只有结构完整、非空、轨道已知的
  Block 才保留，其余记入 `discarded`。保留的 Block 原样拷贝字节（不转码、
  不改内容），Cluster 容器长度会被重写为固定值。
* **索引重建**：SeekHead 的 SeekPosition 与 Cues 的 CueClusterPosition 均相对
  输出 Segment payload 起点；CueRelativePosition 相对 Cluster 元素起点。
  CueTime = Cluster Timestamp + 有符号 Block 相对时间。源端 SeekHead/Cues
  （可能指向已不存在数据）一律丢弃并替换。
* **元数据**：Info 与 Tracks 原样保留，CodecPrivate 不变。缺 Info、Tracks、
  TrackNumber、CodecID 或所需 CodecPrivate，或前端不是合法 EBML 时，抛出带
  字节偏移的可定位错误。

## report.json

```jsonc
{
  "source_size": 50656, "output_size": 50240,
  "segment_payload_position": 47, "timestamp_scale": 1000000,
  "tracks": [{"number":1,"codec":"V_MPEG4/ISO/AVC","kept_codec_private":true, ...}],
  "clusters": 11, "cue_points": 6,
  "recovered": [
    {"source_offset":785,"payload_offset":788,"payload_length":321,
     "track":2,"cluster_timecode":0,"relative_time":0,"display_time_ms":0.0,
     "lacing":0,"frames":1,"frame_sizes":[317],"sha256":"…",
     "container":"SimpleBlock"}
  ],
  "discarded": [
    {"offset":50199,"length":333,"reason":"…"}
  ]
}
```

`discarded` 的 `reason` 覆盖：短读、元素载荷越过 Cluster/文件结尾、无法解析
或为空的 Block、BlockGroup 缺 Block、引用了未知轨道，以及整簇无可恢复块。

## 测试与验收

无第三方依赖，全部用标准库：

```bash
python3 -m unittest tests.test_ebml           # EBML/lacing 单元测试
python3 tests/make_samples.py                 # 生成参考片与 8 份截断样例
python3 tests/acceptance.py                   # 8 样例 + 12 个 seek 目标验收
python3 tests/check_bytes.py                  # 恢复块字节与原片一致
python3 tests/fuzz.py                         # 850 个随机截断点鲁棒性
```

`tests/verify.py` 是一个**独立的纯 Python 解复用器**（不 import 修复器），
用于从外部读取原始/修复容器、模拟按时间 seek、并校验所有 SeekHead/Cue 指针
都指向可解析对象。`tests/make_samples.py` 合成的参考片同时包含：视频
SimpleBlock 关键帧与 BlockGroup 帧、三种 lacing 的音频、固定与未知大小的
Cluster、未知大小 Segment。

验收覆盖的 8 类边界：Cluster 内 Block 中间、Cluster 边界、lacing 音频帧
中间、Info 与 Tracks 之间、Segment 头内部、未知大小 Cluster 内、未知大小
Cluster 边界、末尾 Cues 内部。前两者之外的前置元数据缺失场景会得到可定位
拒绝，其余均在完整原片子集范围内逐块比对包内容哈希、轨道号与显示时间。

> 说明：当前环境无 ffmpeg/mkvtoolnix（且网络禁用），验收以等价能力的独立
> 纯 Python 解复用/seek 校验器替代；其 seek 语义遵循 Matroska Cues 规范。

