# 回填区间预留

用 `python3 reserve.py input.json output.json --journal journal.jsonl` 执行一批本地事件。输入例子在 `sample.json`。

`reserve.py` 管理命令行、日志和输出。`reservations.py` 把日志事件转换为租约状态。现有工具按区间线性查找，崩溃恢复时直接拼接新旧事件。请按需求补全语义。
