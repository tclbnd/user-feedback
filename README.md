# 设备用量明细分批归集
用法：`python3 ingest.py input.json state.json`，仅标准库。输入为 `{"batches":[[行, ...], ...]}`。每行字段为 `event_id`（非空字符串）、`rev`（非负整数）、`source`（primary 或 secondary）、`day`（YYYY-MM-DD）、`device`（非空字符串）、`units`（非负整数）、`op`（upsert 或 delete）。撤销也携带这些字段，但不贡献用量。不存在状态文件时从空状态开始，已有状态文件时增量处理。
每个 event_id 的获胜行按 (rev, source 优先级) 最大者确定，primary 高于 secondary。同一 event_id、rev、source 的内容须逐字段一致，即使那一版已失去优先权。输入内任何一批存在冲突或字段不合法，整次 CLI 调用退出 2，既有状态保持原样。输出单个 JSON，包含：
- `rows`：按 event_id 排序的有效获胜行数组。
- `totals`：以 day、device 分层的对象，叶子为 `{"units":整数,"count":整数}`，无有效明细的设备和日期均省略。
- `changes`：本次调用的每批一个对象 `{"days":[日期, ...]}`。比较批前批后有效明细，列出其归属实际发生变化的旧日期及新日期，去重并排序。获胜行的值变化但汇总碰巧相同仍记录，最终状态不变则为空。撤销不出现在 rows。
- `seen`：所有已经见过且合法的唯一版本行，以 (event_id, rev, source) 排序。跨调用凭它识别历史版本冲突与重复。
现有实现只示范了文件入口和逐行相加，漏掉获胜版本、撤销、跨日和历史去重。
