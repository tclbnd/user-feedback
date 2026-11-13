# 会话窗口与迟到事件

用 Python 3.9+。运行 `python3 sessions.py apply demo.json state.db` 或 `python3 sessions.py inspect state.db`。db 可用 SQLite。

输入含 `partitions`、整数 `gap` 与 `lateness`，以及不可改写、可追加的 `events`。所有时间用同一整数单位。事件分为 `event`（含 partition、key、t、全局唯一 id）、`watermark`（partition、t）、`idle` 和 `resume`（partition）。空闲分区不参与全局最小 watermark。活动分区没有报告 watermark 时，全局 watermark 保持原值。恢复后全局 watermark 不倒退，全空闲时保持原值。每个分区自己的 watermark 也不倒退。

输入事件 ID 重复时忽略后续出现。新事件若 `t + gap + lateness < 全局 watermark` 则进 late 列表，等于边界时仍接受。接受事件按 key 和事件时间分组，相邻间隔 ≤ gap 的事件属于同一会话。会话最后事件时间加 gap ≤ 全局 watermark 时对外可见。已发布会话被迟到事件扩展、替换或重新打开时，撤回旧行，再按可见规则添加新行。允许多个迟到事件先后合并三个原会话。

JSON 输出至少有 processed、watermark、rows、batches、late。rows 行的固定字段为 key、start、end、event_ids，start/end 为首末事件时间，event_ids 按字典序。late 是按接收顺序排列的事件 ID。每个可见变化批次有 seq（触发源事件 1 基序号）和 changes（含 op 及 row），op 为 retract 或 upsert。批次只在有变化时追加，所有 retract 位于所有 upsert 前，每组按 key、start、end 排序。可增加字段保留触发来源。一个批次及其进度对读者同时可见。

`--limit N` 指累计处理到第 N 条源事件。重读同样的前缀不会生成新批次。给 `--crash-during-merge` 时，在首个至少撤回两条再新增一条的批次中，暂存撤回后、完整批次可见前无清理退出 97。恢复检查点后按原始 seq 重做。
