# 会话窗口与迟到事件

用 Python 3.9+。运行 `python3 sessions.py apply demo.json state.db` 或 `python3 sessions.py inspect state.db`。db 可用 SQLite。

输入含 `partitions`、整数 `gap` 与 `lateness`，以及不可改写、可追加的 `events`。所有时间用同一整数单位。事件分为 `event`（含 partition、key、t、全局唯一 id）、`watermark`（partition、t）、`idle` 和 `resume`（partition）。空闲分区不参与全局最小 watermark。活动分区没有报告 watermark 时，全局 watermark 保持原值。恢复后全局 watermark 不倒退，全空闲时保持原值。每个分区自己的 watermark 也不倒退。

输入事件 ID 重复时忽略后续出现。新事件若 `t + gap + lateness < 全局 watermark` 则进 late 列表，等于边界时仍接受。接受事件按 key 和事件时间分组，相邻间隔 ≤ gap 的事件属于同一会话。会话最后事件时间加 gap ≤ 全局 watermark 时对外可见。已发布会话被迟到事件扩展、替换或重新打开时，撤回旧行，再按可见规则添加新行。允许多个迟到事件先后合并三个原会话。

JSON 输出至少有 processed、watermark、rows、batches、late。rows 行的固定字段为 key、start、end、event_ids，start/end 为首末事件时间，event_ids 按字典序。late 是按接收顺序排列的事件 ID。每个可见变化批次有 seq（触发源事件 1 基序号）和 changes（含 op 及 row），op 为 retract 或 upsert。批次只在有变化时追加，所有 retract 位于所有 upsert 前，每组按 key、start、end 排序。可增加字段保留触发来源。一个批次及其进度对读者同时可见。

`--limit N` 指累计处理到第 N 条源事件。重读同样的前缀不会生成新批次。给 `--crash-during-merge` 时，在首个至少撤回两条再新增一条的批次中，暂存撤回后、完整批次可见前无清理退出 97。恢复检查点后按原始 seq 重做。

## 状态模型与检查点

状态文件是一个原子替换的 JSON 文档（先写 `.tmp` 再 `os.replace`），同时恢复三类位置：

- `partitions` / `watermark`：各分区 watermark、idle 标记与全局 watermark；
- `sessions` / `seen`：每个 key 当前的会话区间（含按字典序的 event_ids）与去重集合；
- `rows` / `batches` / `last_seq` / `last_batch_seq`：下游可见行、不可变批次日志和输出位置。

每个批次额外带 `batch`（批次序号）、`source`（触发事件的 seq、kind、partition、key/id/t 等来源字段）和批次时的 `watermark`。重复 ID 与过期迟到事件不产生批次，但仍推进检查点并在 `source.dedup` 对应步骤留下 `duplicate-ignored` / `late-expired` 去向；过期事件进入 `late` 列表。

合并在内存中按事件时间循环归并相邻区间直到不再变化，因此单个迟到事件可以桥接任意多个已发布会话，连续迟到事件可以跨多个区间传递合并。撤回按整行值（key,start,end,event_ids）差分，保证每条 retract 都对应下游当前持有的行，没有孤儿撤回。

## 崩溃恢复

`--crash-during-merge` 命中首个“≥2 条 retract + ≥1 条 upsert”的批次时，引擎把撤回部分写入 `state.db.staging` 并以退出码 97 退出，主检查点仍停留在该事件之前。下次 `apply` 会删除 staging、从检查点恢复 watermark/区间/输出位置，并按该事件原始 1 基 seq 重做完整批次；staging 中的撤回从未对读者可见，因此不需要补偿。

## 测试与独立离线对照

- `python3 -m unittest test_acceptance -v`：覆盖两个迟到事件合并三个原窗口、分区 idle/resume、重复事件、过期迟到、逐步撤回无孤儿、崩溃恢复、两次回放物化表一致、每个消费批次后的会话区间约束。
- `offline_reference.py` 是不导入引擎的独立离线分组实现，可用 `python3 offline_reference.py demo.json` 对照最终行集合与 late 列表。

