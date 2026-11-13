# 历史 Parquet 字段对齐

Python 3.9+，依赖 requirements.txt。运行 `python3 make_demo.py input` 后运行 `python3 import_history.py input output.json`。程序扫描输入目录的 `*.parquet`，先按文件名排序，因此目录枚举或调用方提供文件的顺序不会影响结果。

## 字段 ID 对齐

- 逻辑 schema 的 ID 存放在 Arrow Field 的 `PARQUET:field_id` 元数据中，并递归处理 struct 与 list 内 struct 的子字段。
- 名称只用于读取当前 schema 中的物理列，不参与跨 schema 匹配。旧字段改名后，只要 ID 不变就会对齐到同一目标字段；复用旧名称但使用新 ID 的列不会替代旧 ID。
- 目标 schema 固定为：ID 1 `id` 字符串；2 `title` 字符串，字段缺失时默认 `"untitled"`；3 `score` 定点小数；4 `items` 列表；5 为 `items[].name`；6 为 `items[].amount`；7 `utc` 时间戳。
- 未出现在目标 schema 中的带 ID 列会被忽略。重复 ID、重复物理名、嵌入 ID 与旁挂映射冲突，都会确定性地产生文件级 `AMBIGUOUS_SCHEMA` 诊断。
- 缺少 ID 的历史文件可使用同目录、同 basename 的 `.ids.json`，把点分物理路径映射到整数 ID，例如 `{"entries.name": 5}`。顶层路径精确匹配，因此包含点号的字面列名也能显式映射。无 ID 且无法映射的非系统列（不匹配 `__name__` 形式）会拒绝整个文件，并列出具体字段路径；映射到未知目标 ID 的列与其他未知 ID 列一样忽略。
- 缺字段、显式 null 和空列表是三种不同语义：列或嵌套字段缺失才应用默认值；显式 null 保留 null；空列表保留 `[]`。序列中的 null 元素也保留 null。

## 类型规范化与隔离

- `score` 与 `amount` 输出为两位十进制字符串。转换使用 `Decimal` 和 `ROUND_HALF_EVEN`；扩精只补零，缩精按银行家舍入，舍入后超出 `[-9999.99, 9999.99]` 的记录按 `DECIMAL_OVERFLOW` 隔离。
- `utc` 只接受带时区的 Arrow timestamp，单位允许 `s`、`ms`、`us`、`ns`。程序先转换到 UTC，再输出以 `Z` 结尾的 ISO 8601；整秒且小数为零时省略小数部分，其他情况保留源单位的小数宽度。不带时区或单位/语义不受支持时使用 `TIMESTAMP_SEMANTICS`。
- 一行中的所有字段级错误都会收集后再隔离整行；拒绝记录至少包含 `{file,row,field,reason}`，并在可用时附 `source` 与说明。`row` 从 1 开始，文件级诊断的 `row=null`。
- 其他常见代码：`MISSING_FIELD`、`TYPE_MISMATCH`、`AMBIGUOUS_SCHEMA`、`DECIMAL_OVERFLOW`、`TIMESTAMP_SEMANTICS`、`UNREADABLE_FILE`。

## 输出与清单

输出顶层字段为：

- `rows`：规范化逻辑记录，按 `id` 排序；列表保持源顺序。
- `rejects`：文件级和行级拒绝记录，按文件名、行号、目标字段路径、诊断内容确定性排序。
- `sources`：按文件名排序的输入文件与 schema 版本清单，包含物理行数、接受/拒绝行数、ID 映射方式、旁挂映射文件名、递归字段描述，以及由规范化字段描述计算出的 `sha256:` schema 版本。

重复导入相同输入会生成相同 JSON。测试使用 `python3 -m unittest -v test_import_history.py`，覆盖四代 schema、嵌套 ID 改名、同名不同 ID、字面点号列名、旁挂映射、显式 null/空列表/缺字段、十进制扩精与缩精、时间戳单位、随机打乱文件顺序，以及错误定位。
