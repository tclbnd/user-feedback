# 原生 PDF 续表还原

Python 3.9+，依赖 requirements.txt。执行 `python3 table_pdf.py demo.pdf output.json --batch-pages 2`。输入是有文本层的合成 PDF，不做 OCR。

## 为什么不用文本流

旧前端按 `extract_text()` 的流顺序取行，遇到双栏会串行、把续页表头当数据、把跨页描述接到下一条记录。本引擎改为**坐标驱动**：

1. **内容流直读**：自行解析 `BT/ET/Tm/Td/T*/Tj/TJ/Tf/TL/cm`，按 PDF 文本矩阵计算每个文本段基线起点。pypdf 的 `visitor_text` 对部分字形会报 (0,0) 错矩阵，故不采用。页面 `/Rotate` 只影响查看器显示、不改内容流坐标，因此来源坐标始终是**未旋转页面的原 PDF 坐标**，无需旋转校正。
2. **几何重建**：按 `KEY/DESCRIPTION/AMOUNT` 表头三元组锚定栏区（同基线上的两块按 KEY 循环切分），用相邻表头中点建立列边界与列宽；标题（表头之上）与页脚（正文之下超过间隙）按垂直位置排除；栏间沟槽里无法归属的片段进 `unassigned` 诊断，绝不静默丢弃。
3. **阅读顺序与续行**：页码 → 左栏 → 右栏 → 自上而下。同一基线的字段按 y 容差聚成行，允许小幅漂移、稀疏列和描述换行。新栏/新页顶部的 `CONT:` 只接到阅读顺序中上一条仍开放的记录，单空格拼接；普通无 KEY 描述行接到本栏当前记录。
4. **类型化与隔离**：KEY 必须匹配 `R###`，金额为整数或空（空字段输出 `null` 且 `source.amount=[]`，与“无法确定”区分）。同一行出现两个不同金额 → `AMBIGUOUS_AMOUNT`；无归属的 CONT/描述行 → `ORPHAN_CONTINUATION`；另有 `BAD_KEY`、`UNPARSEABLE_AMOUNT`、`UNCLASSIFIED_ROW`。每条拒绝都带 `page/block/y/texts/fragments/evidence`，歧义行隔离不进数据，其余记录保持原顺序。
5. **可分页、可重跑**：文档按页处理，`Assembler` 在批次边界携带状态，任意 `--batch-pages` 输出逐字节一致（`meta.batch_pages` 字段本身除外）。`--start-page N` 支持从任意页重启，输出该页到文末的后缀，与整文档一次处理时对应区间一致。`meta.sha256` 标识文档版本，供按版本重跑比对。

## 输出结构

顶层 `{rows, rejected, meta}`：

- `rows[]`：`{id, description, amount, source}`；`source` 把 `id/description/amount` 映射到一个或多个 `{page,x,y}`，页从 1 开始，x/y 为各文本段基线起点（pt，保留 3 位小数）。
- `rejected[]`：至少含 `reason` 与 `page`，并附来源框与证据文本。
- `meta`：`sha256`、`pages`、`start_page`、`batch_pages`、`column_widths`（每块每列 x0/x1/width）、`page_breaks`（每页栏区、表头 y、标题/页脚/unassigned 框、列宽）、`rejection_diagnostics`（计数与原因集合）。

## 验收

`python3 verify_extract.py` 以 `make_demo.py` 的生成器逻辑表为独立真值，覆盖 6 个版式变体 × 干净/故障共 12 份跨页双栏 PDF：记录数、每格值、跨页合并位置、来源框坐标（误差 0 pt，验收阈值 ≤2 pt）；重复页头不进数据；两种有意歧义（重复金额 28/777、无主 `CONT: stray`）各精确隔离一次并带证据；不同批次（1/2/3/5/100）与每个重启点结果一致；含旋转页、乱序流、换栏、稀疏列与描述换行。演示 PDF 由 `make_demo.py` 生成，包含跨页续行、换栏、乱序和旋转。

