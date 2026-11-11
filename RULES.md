# 流式 NDJSON 采集入库链路 — 处理规则与设计说明

本文件是链路的明示规则（specification）。代码（`ndjson_pipe/normalize.py` 等）与
差分回归测试都以这里描述的规则为准。

## 1. 源、分片与字节偏移

- 一个逻辑源（source）有稳定的 `source_id`。供应商按天投递的分片（shard）是同一
  逻辑源的字节追加；重复投递是同一逻辑源的再次全量投递。
- 所有偏移量都是 **UTF-8 字节偏移**，不是字符偏移。多字节字符可以跨越读取块，
  解码只在 `\n` 处切分记录，绝不在字符中间切。
- 物理行以 `\n` 结束，CRLF 的 `\r` 在内容中剥离；行终止符不参与内容哈希。
- 一行一个 JSON 文档（NDJSON）。空物理行不产生记录，但仍是已处理字节，会推进断点。
- 文件**未封口**（`sealed=false`）时，末尾没有 `\n` 的残行保留在解码器缓冲区，
  不解析、不落库；下一次追加补齐后作为同一行处理。封口后残行作为最后一行处理。

## 2. 流式解码（`ndjson_pipe/decoder.py`）

- 面向字节的增量行切分器 `NDJsonStreamDecoder`，每次 `feed(chunk)` 产出已完整的
  `RawLine(line_no, start_offset, end_offset, raw)`。
- 内存只保留：当前残行缓冲 + 当前事务批次，绝不保留整个文件。
- 支持从断点构造（`start_offset`、`start_line_no`），恢复后行号连续。

## 3. 归一化明示规则（`ndjson_pipe/normalize.py`）

同一批次混用两种 schema 版本：

| 版本 | 金额字段 | 形式 |
|----|--------|----|
| 1 | `amount` | 十进制**字符串** |
| 2 | `amount_cents` | 整数分 |

两者都归一化为精确 `decimal.Decimal`（两位小数，`quantize(0.01)`）和整数
`amount_cents`，金额全程不经过二进制浮点。

### 3.1 歧义情形的明示处理

| 情形 | 规则 | 拒收码 |
|----|----|----|
| JSON 任意层级出现重复键 | 拒收，报告第一个重复键名 | `duplicate_key` |
| 非法 UTF-8 字节 | 拒收 | `encoding_error` |
| JSON 语法错误 | 拒收 | `json_syntax_error` |
| 顶层不是对象 | 拒收 | `not_object` |
| 缺失必填字段（`id`/`version`/`currency`/金额） | 拒收并报字段名 | `missing_field` |
| 必填字段显式为 `null` | 拒收并报字段名（区别于缺失） | `null_field` |
| 字段类型错误（布尔永不当整数） | 拒收并报字段名 | `bad_type` |
| 未知 `version` | 拒收（门控由归一化注册表派生） | `unknown_version` |
| v1 金额不是纯十进本文本 | 拒收（拒绝指数、内嵌空格等） | `amount_format` |
| v1 金额小数位 > 2 | **拒收，不静默四舍五入** | `amount_precision` |
| 金额超出声明精度范围（±`9999999999.99`） | 拒收 | `amount_range` |
| 币种不是 3 个大写 ASCII 字母 | 拒收 | `currency_format` |
| `id` 为空字符串 | 拒收 | `bad_type` |
| `note` 缺失 vs 显式 `null` | **严格区分**：缺失时 `note_present=false`；显式 null 时 `note_present=true, note_value=null` | — |
| 未知/前向兼容字段 | 忽略，新旧字段可混批而不改变旧行 | — |

### 3.2 版本钉住（history 不变性）

- 每条入库记录带 `version`（schema 版本）和 `normalized_by`（实际使用的归一化器版本）。
- 归一化器是按版本注册的不可变函数表 `_NORMALIZERS`。历史记录永远由它自己版本的
  归一化器重新归一化。
- 新增 v3 只在注册表注册新函数并自动放宽版本门控，**不得修改** v1/v2 归一化器；
  因此新增版本字段不会改变任何历史行的含义。`tests/test_version_pinning.py`
  通过注入一个模拟 v3 证明这一点。

## 4. 入库与事务（`ndjson_pipe/store.py`、`importer.py`）

四张表，全部在同一 SQLite 事务中推进：

- `records`：接受的归一化记录，金额以 TEXT 存精确十进制，另存 `amount_cents`；
- `rejections`：结构化拒收原因（`reason`/`field`/`detail`）+ 原始行 JSON 样本；
- `conflicts`：同一 `(source_id, start_offset)` 出现不同内容；
- `checkpoints`：每个源一行，`next_offset` + `last_line_no`，单调不减。

### 4.1 幂等与冲突

- 内容身份是行内容（不含行终止符）的 SHA-256。
- 已处理位置再次出现**相同内容**：跳过，不计新行（`replayed_identical`）。
- 同一位置出现**不同内容**：写 `conflicts`，绝不覆盖既有记录/拒收，历史保持不变。
- 断点是已持久化的最大字节位置，单调；全量重扫较短文件不会把断点回退。

### 4.2 崩溃注入点（验收用，`PIPE_CRASH_POINT`）

| 注入点 | 时机 |
|----|----|
| `before_persist` | 解析成功之后、写入任何数据之前 |
| `in_transaction` | 事务内写完、`commit` 之前 |
| `after_commit` | 事务 `commit` 成功之后 |

注入通过 `os._exit(42)` 硬终止进程（无 finally/atexit/缓冲刷新），模拟 `kill -9`。
重放原输入后，记录、拒收、冲突、断点必须与无崩溃基线完全一致。

## 5. 独立批处理参考实现（`ndjson_pipe/reference.py`）

- 一次性读入整个封口文件，用独立的索引循环 `batch_split_lines` 切行、独立计算偏移，
  与流式解码走不同代码路径。
- 两者只共用规则层 `normalize_line`（规则本身即规范）。
- 支持两种期望模型：单文件 `expected_for_source` 与多次独立投递
  `expected_for_deliveries`（每次投递偏移空间共享、物理行号从 1 重新计数）。
- 差分测试比较记录/拒收/冲突的完整行元组集合（含偏移、版本血统、内容哈希）与断点。

## 6. 语料与覆盖反馈（`tests/corpus.py`、`tools/coverage_feedback.py`）

- **合法语料**：种子化随机生成，优先用于扩展结构与块切分组合（多语言 note、
  null/缺失 note、正负零金额、边界金额、前向兼容字段、CRLF、空行等）。
- **业务拒收**：固定手写样例 `FIXED_REJECTS`（25 条，覆盖全部拒收码），不随机生成，
  防止拒收分类随生成器漂移。
- 覆盖率用标准库 `trace` 驱动，只统计四个核心模块的可执行语句起始行（AST 口径，
  排除文档字符串），阈值 95%；未达标时要求先扩合法语料而非改固定拒收样例。

## 7. 内存验收（`ndjson_pipe/memory_acceptance.py`）

- 用 `tracemalloc` 测量 Python 层峰值分配。
- 数据源远大于读取缓冲区（10× 数据量、256 字节块），峰值增长比必须 < 1.5 且
  峰值 < 文件大小的 1/4，证明不会整文件缓冲。
- 注意 `tracemalloc` 只统计 Python 分配，不含 SQLite/C 层缓冲。
