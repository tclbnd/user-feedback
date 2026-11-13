# 列级血缘与影响分析（SQL → 报表列）

定位"改动一个源字段会影响哪些报表列"。按查询作用域解析列引用，
区分**输出值依赖**（value）与**行存在性依赖**（row），输出带 AST
源位置的依赖图、歧义/缺 schema/不支持语法的精确报告与保守影响范围，
并维护可增量重算的查询依赖索引。

## 模块

| 文件 | 职责 |
| --- | --- |
| `sqlfront.py` | SQLite SELECT 方言子集的词法/语法分析；每个 AST 节点带 1-based (line, col)；不支持的语法抛出带位置的 ParseError |
| `analyzer.py` | 作用域感知的列解析与依赖提取（核心） |
| `lineage_index.py` | 依赖索引：单查询/单表 schema 变更的增量重算、影响计算、确定性导出 |
| `queries/q01..q32.sql` | 32 条覆盖用例 |
| `annotations.py` / `annotations.json` | 逐列人工标注的期望依赖集合与类型、期望报告位置 |
| `acceptance_static.py` | 验收 1：依赖集合+类型+报告位置 vs 标注 |
| `acceptance_index.py` | 验收 3：增量重算集合、证据路径、可重复导出 |
| `acceptance_dynamic.py` | 验收 2：受控改值/行增删 + 独立 SQLite 执行验证 |
| `seed.py` | 确定性合成数据与变更算子 |

## 依赖类型约定

- **value**：SELECT 表达式（含聚合参数、窗口 OVER 子句、标量子查询
  的全部内部依赖）决定输出列的取值。
- **row**：JOIN ON/USING、WHERE、GROUP BY、HAVING、DISTINCT（整行
  去重 → 合并全部输出列的值依赖）、LIMIT+ORDER BY、集合运算去重，
  以及从 CTE/派生表源提升的行依赖；另外每个被扫描的基表贡献一条
  仅行级（rows-only）的 `t.*` 依赖——行的增删会改变输出成员，
  即使该表没有任何列被引用。`COUNT(*)` 的值同样只依赖行集合。
- `t.*` 两种形态：rows-only（表扫描、COUNT(*)，仅匹配行增删）与
  保守形态（schema 未知、不支持语法，匹配该表任何变更）。

## 作用域解析要点

- FROM 源按顺序解析：基表（schema 校验）、CTE（惰性+记忆化，内层
  WITH 遮蔽外层，非递归 CTE 体不可见自身 → q10/q12 遮蔽正确）、
  派生表（输出列即源列，行依赖提升到使用方作用域）。
- 未限定列：唯一候选解析；多候选 → `ambiguous` 报告（精确到列名
  词素的行列号）+ 依赖全部候选（保守）；无候选 → 外层作用域
  （相关子查询，证据链标注 correlated）→ 否则 `unresolved_column`
  + 依赖本作用域全部列（保守）。
- 限定列：别名/表名匹配源；缺表 → `missing_table`（引用按字面记录
  并对星号保守展开）；缺列 → `missing_column` + 依赖该表全部列。
- 星号：按源顺序展开；t.* 限定展开；schema 未知时生成保守输出列。
- 表达式级子查询（标量/EXISTS/IN）以当前作用域为外层解析，其输出
  的 value+row 依赖按使用上下文合并（SELECT 内 → value，WHERE 内
  → row）。
- 递归 CTE 等不支持语法 → `unsupported` 报告 + 输出列保守依赖
  CTE 体内全部基表的全部列（value+row）；解析失败 → 全部输出
  依赖全 schema（最保守）。

## 增量索引与导出

- `set_query`：只重算该查询节点；`update_schema`：只重算引用了该表
  的查询（含未解析的缺表引用）。
- `impact(changes)`：变更三元组（table, col, kind=column|rows|schema）
  → 受影响输出列及其 kinds 与证据路径（使用点 → CTE/子查询跳转 →
  定义点的 AST 位置链）。
- 导出（`out/`）：`lineage_graph.json`（全量依赖图）、
  `impact_orders_amount.json`（影响报告）、`dynamic_validation.json`
  （动态验证日志）。含 manifest（schema/查询的 sha256、查询数），
  排序序列化，重复运行字节一致。

## 验收（`./run_all.sh`）

1. **静态**：32 条查询、83 个标注输出列的依赖集合与类型全部一致；
   歧义（q29 @1:14）、缺表（q30 @2:6）、递归 CTE（q31 @1:16）报告
   位置精确。覆盖 CTE 遮蔽（q10/q12）、相关子查询（q13/q14/q32）、
   外连接（q04/q25/q26）、星号（q05/q06/q27）、集合运算（q17/q18）
   等。
2. **动态**：36 个受控变更（32 列改值 + 4 行增删）× 29 条可执行
   查询，独立 SQLite 执行观察。每个观察到的变化都能被血缘解释
   （observed ⊆ predicted；行数变化必须有 row 级依赖）；7 个预先
   设计的无关变更（events.*、shipments.shipped_at）预测与实际重算
   集合均为空。预测是保守超集（321 vs 227 个影响单元）。
3. **索引**：单查询变更只重算自身；customers schema 变更精确重算
   17 条引用查询；导出可重复。
