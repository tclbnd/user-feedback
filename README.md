# SQL 列级血缘

Python 3.9+，依赖 requirements.txt。执行 `python3 lineage.py demo.json report.json`。输入 schemas={物理表名:[字段名]}、queries=[{id,sql}]，SQLite 方言子集为 SELECT、别名、非递归 CTE、FROM 子查询、星号、JOIN（含 LEFT）、WHERE、GROUP BY、聚合、相关子查询和 EXISTS。识别作用域遮蔽与关联引用。输出 queries 按输入顺序，查询含 id、columns、issues。columns 顺序与投影相同，每项 {name,value,row,unknown}，value 是影响输出字段值的物理列，row 是影响输出行或是否匹配的物理列。列表升序去重。若某字段在外连接右表，连接键仍属于该字段的 row；允许额外把决定 null 扩展的连接键并入 value。

固定定义：直接输出字段的 value 为表达式可达物理列。JOIN ON、WHERE、GROUP BY 的物理列进入本查询所有输出的 row。内部来源查询的 row 传播到引用其字段的输出。相关 EXISTS 中被 SELECT 但未影响存在性的表达式不传播；其 JOIN 与过滤字段传播到外层 row。COUNT(*) 不引入任意值列。相同名称未限定且当前作用域有多张表提供字段时，unknown=true，给出 AMBIGUOUS_COLUMN，并列出可能依赖。缺表/缺列或不支持语法使用可定位 issues，含 code、line、column（从 1 开始）。可增加 AST 来源和变更影响字段；对可解析查询必须给出稳定结果。
