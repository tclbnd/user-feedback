# 数仓日分区清单发布
用法：`python3 publish.py request.json warehouse`。输入字段为 `batch_id`（非空字符串）、`expected_version`（整数，首次为 0）、`partitions`（下列对象的非空数组），可选 `stop_after_stage`（布尔，默认 false）。
每个分区对象包含 `day`（YYYY-MM-DD）、`path`（相对于请求文件的路径）、`sha256`（小写完整摘要）、`rows`（行数）。文件为 UTF-8 JSONL，每一行应为对象且 day 与分区相同。同一批不可重复 day。
读者用 `warehouse/CURRENT.json`（`{"version":整数,"manifest":"相对仓库路径"}`）找到不可变清单。清单格式为 `{"version":整数,"batch_id":字符串,"partitions":{day:{"batch_id":字符串,"path":相对仓库路径,"sha256":字符串,"rows":整数}}}`。消费者可以在切换后继续使用之前已读到的清单。版本从 0 起，每次成功发布加 1，首批之前无 CURRENT。回填只替换请求中的日期，未动分区保留原来源。
同一 batch_id 以 expected_version 和按日期排序的 (day, sha256, rows) 作为身份，输入路径和中止开关不参与身份比较。成功批次重放退出 0，CURRENT 无变化，即使已经有更高版本。同批不同身份或新批期望版本过时退出 3。数据校验失败退出 2。
`stop_after_stage=true` 在校验、持久化暂存后退出 75，CURRENT 不变。恢复用同一身份请求，即使移走原始文件也能从仓库暂存发布。两个独立进程同时提交同一 expected_version 时，只有一个切换成功。
现有实现是读取请求并直接覆盖 CURRENT 的示例，尚未保存不可变清单、校验数据或处理重试。
