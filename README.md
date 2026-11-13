# 历史 Parquet 字段对齐

Python 3.9+，依赖 requirements.txt。运行 `python3 make_demo.py input` 后 `python3 import_history.py input output.json`。扫描输入目录的 *.parquet。逻辑 schema 的 ID 存放在 Arrow Field 的 PARQUET:field_id 元数据，包含列表内 struct 的子字段。目标 ID 1 为 id 字符串，2 为 title 字符串，3 为 score，4 为 items 列表，5 为 items 元素内 name，6 为元素内 amount。目标 score 与 amount 都为定点小数，2 位小数，最大 9999.99，ROUND_HALF_EVEN。序列中的 null 列表元素保留 null。缺 score、items 输出 null；缺 title 输出 "untitled"。已知列 title 的 null 保持 null。未识别的带 ID 列忽略，不能用同名新 ID 代替旧 ID。目标输出小数为两位十进制字符串。

目标 ID 7 为 utc。只接受带时区的 Arrow timestamp(s/ms/us/ns)；输出 UTC ISO 8601，末尾 Z，无小数时省略小数。没有该字段或 null 输出 null。若对应 schema 的时钟不带时区，或数值超出精度/最大值，则隔离整行。针对缺 ID 的历史文件，可使用旁边的同名 .ids.json，把点分路径映射到 ID。无 ID 又无法映射的非系统列使该文件进入拒绝清单，reason=AMBIGUOUS_SCHEMA。未支持的时间字段 reason=TIMESTAMP_SEMANTICS，十进制溢出 reason=DECIMAL_OVERFLOW。行级拒绝记录至少为 {file,row,field,reason}，row 从 1 开始。文件级 row=null。

输出 rows（按 id 排序）、rejects（按文件与行排序）和 sources（按文件名排序，可含 schema 摘要）。列表字段保持源顺序。输入目录文件枚举顺序不得影响逻辑结果。所有文件用合成数据。
