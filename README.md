# 历史 Parquet 字段对齐

Python 3.9+，依赖 requirements.txt。运行 `python3 make_demo.py input` 后 `python3 import_history.py input output.json`。扫描输入目录的 *.parquet。逻辑 schema 的 ID 存放在 Arrow Field 的 PARQUET:field_id 元数据，包含列表内 struct 的子字段。目标 ID 1 为 id 字符串，2 为 title 字符串，3 为 score，4 为 items 列表，5 为 items 元素内 name，6 为元素内 amount。目标 score 与 amount 都为定点小数，2 位小数，最大 9999.99，ROUND_HALF_EVEN。序列中的 null 列表元素保留 null。缺 score、items 输出 null；缺 title 输出 "untitled"。已知列 title 的 null 保持 null。未识别的带 ID 列忽略，不能用同名新 ID 代替旧 ID。目标输出小数为两位十进制字符串。

目标 ID 7 为 utc。只接受带时区的 Arrow timestamp(s/ms/us/ns)；输出 UTC ISO 8601，末尾 Z，无小数时省略小数。没有该字段或 null 输出 null。若对应 schema 的时钟不带时区，或数值超出精度/最大值，则隔离整行。针对缺 ID 的历史文件，可使用旁边的同名 .ids.json，把点分路径映射到 ID。无 ID 又无法映射的非系统列使该文件进入拒绝清单，reason=AMBIGUOUS_SCHEMA。未支持的时间字段 reason=TIMESTAMP_SEMANTICS，十进制溢出 reason=DECIMAL_OVERFLOW。行级拒绝记录至少为 {file,row,field,reason}，row 从 1 开始。文件级 row=null。

输出 rows（按 id 排序）、rejects（按文件与行排序）和 sources（按文件名排序，可含 schema 摘要）。列表字段保持源顺序。输入目录文件枚举顺序不得影响逻辑结果。所有文件用合成数据。

## 实现约定

### 字段对齐

- 递归遍历顶层字段、struct 子字段与 list 元素（含 list 内的 struct 和嵌套 list）。ID 只从 Arrow Field 的 `PARQUET:field_id`（兼容 `field_id`）读取；名称从不参与身份判定。
- 目标 ID 的位置是固定的：1/2/3/4/7 必须在顶层，5/6 必须在 ID 4 的列表元素内。目标 ID 出现在其它位置记 `UNEXPECTED_ID_PLACEMENT`，整个文件进拒绝清单，避免静默丢弃或错并。
- 同一文件内目标 ID 重复记 `DUPLICATE_FIELD_ID`。非目标 ID 的重复列按约定忽略。
- 缺 ID 的列先尝试 `.ids.json` 点分路径映射（可省略 list 的 `element` 包装层）。映射键不存在、与嵌入 ID 冲突、重复键、非法 JSON 都会使文件进入拒绝清单；未映射的非结构列记 `NO_ID`。
- 拒绝清单中的 `hint` 会汇总整个输入目录中同名清单：若同名在不同代承载了不同 ID，会明确写出"按名称导入会把它们合并"。名称只用于解释，从不用于对齐。

### 数值

- score/amount 目标为 decimal(6,2)：先按 ROUND_HALF_EVEN 量化到 2 位，再检查 |v| ≤ 9999.99。所以 9999.995 → 10000.00 属于溢出，9999.994 → 9999.99 通过；整数源按 scale 扩展（7 → "7.00"），高位 scale 源按 scale 收缩（0.1250 → "0.12"，0.1350 → "0.14"）。负零输出 "0.00"。
- 溢出按行隔离，`field` 为 `score` 或 `items[i].amount`，`source` 是源列点分路径，`detail` 写明原值与舍入后的超界值。

### 时间

- 只接受带时区的 s/ms/us/ns。读取用底层整数 epoch，避免 datetime 的微秒截断和 ns 越界异常。
- 合法区间为 0001-01-01T00:00:00Z 至 9999-12-31T23:59:59.999999999Z（保证四位年份可渲染）。区间外先判断"整数是否其实属于更细的单位"：若声明 ms 但整数落在 us/ns 的合法域，`detail` 报 `value fits us not declared ms`，`reason=TIMESTAMP_SEMANTICS`；否则报超出支持区间。
- 不带时区、非 timestamp、单位不支持的列同样按行隔离，`reason=TIMESTAMP_SEMANTICS`。
- 输出裁剪尾随零：`2026-01-01T00:00:00Z`、`...:00.5Z`、`...:00.123456Z`。

### 清单与可重复性

- `sources` 每个文件给出 `file_sha256`、`schema_digest` 与规范化 `schema`（按路径排序的 `{path,id,type,nullable}`）；`manifest.schema_versions` 按 digest 汇总同代 schema 及文件清单。
- `rows` 按 (id, 文件名, 行号) 排序，`rejects` 按 (文件, 行, 字段, 原因) 排序，文件级 `row=null` 排在该文件行级拒绝之前；因此打乱文件枚举顺序、重复导入的结果完全一致。
- 空列表输出 `[]`，null 列表输出 `null`，字段缺失输出 `null`，三者严格区分。

## 测试

`python3 -m unittest test_import_history -v`（或 `python3 -m pytest test_import_history.py`）。覆盖 4 代 schema 交叉分区、嵌套 ID 改名、同名不同 ID、空列表/缺字段/显式 null、数值进位与溢出、时间单位误标、无 ID 歧义列、sidecar 冲突，以及乱序与重复导入的一致性。
