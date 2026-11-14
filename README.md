# 遥测事件时间窗口与迟到更正
用法 `python3 quota.py req.json output.json`。输入 `{"window":60,"batches":[[事件,...],...]}`。时长 window 为正整数秒。事件字段为 `id` 非空字符串、`rev` 非负整数、`tenant` 非空字符串、`time` 非负整数秒、`value` 非负整数、`op` 为 upsert 或 delete。窗口起点为 floor(time/window)*window，恰好位于边界的事件进入新窗口。相同 id 的最大 rev 胜出，delete 胜出时删除事件。同 id/rev 内容冲突时整个调用退出 2。
输出为 `rows`（有效事件按 id 升序）、`buckets`（按 tenant 字符串、start 数值排序的 `{"tenant":字符串,"start":整数,"count":整数,"sum":整数}` 数组）和 `changes`（每批一个变动窗口数组，元素为 `[tenant,start]`，按同样顺序排序）。窗口变动比较批前批后的 count 和 sum，且统计变动才列出。无事件的窗口不输出。无效输入退出 2，保留旧输出。
现有入口只对有效操作逐行加总，尚未应用修订与去重。
