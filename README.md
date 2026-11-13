# 版本化分位数摘要

可合并的有界分位数摘要 + 完整二叉分区树，替代“把各分区 P95 直接平均”的旧做法。
仅依赖 Python 3.9 标准库。

## 快速开始

```bash
python3 quantiles.py ROOT apply demo.json
python3 quantiles.py ROOT query --start 0 --end 16
python3 quantiles.py ROOT query --start 2 --end 14 --reverse
python3 quantiles.py ROOT migrate --algorithm q2
python3 quantiles.py ROOT stats
python3 -B -m unittest discover -s tests          # 27 个测试
python3 tools/run_acceptance.py                   # 人类可读验收报告
python3 tools/run_acceptance.py --json            # 机器可读报告
```

## 算法与定义（实现要求 1）

* **算法版本**：`gksummary/v1`，Greenwald–Khanna（GK, 2001）元组摘要。每个节点
  存三元组 `(value, g, delta)`：`g` 是该值与前驱值之间的权重，`delta` 是 GK
  不确定度；末尾保留 `+inf` 哨兵，首元组为最小值哨兵。
* **注册版本与误差契约**：

  | algorithm | epsilon | 每节点记录硬上限 cap |
  |-----------|---------|----------------------|
  | `q1`      | 0.001   | 4096                 |
  | `q2`      | 0.0005  | 4096                 |

  epsilon 已按最坏合并顺序（16 路深度-15 左折叠）标定，验收数据上最坏绝对秩
  误差 < 0.3% N，稳定满足 1% 契约。
* **权重**：所有样本权重为 1。
* **并列值**：重复值不产生新元组——插入与合并时同值先聚合（权重相加、delta
  取最大）。并列值的秩按完整秩区间 `[r_min, r_max]` 处理。
* **空集**：`N=0` 时分位数为 `null`，`max_error=0`；空摘要是合并恒等元。
* **分位点（最近秩）**：目标秩 `r = ceil(p*N)`，GK 查询返回第一个满足
  `r_max + delta >= r` 的元组值。
* **误差契约字段 `max_error`**：保守记账，保证真实绝对秩误差
  `|r_hat - r| <= max_error`。周期性压缩保留的旧元组按 `max(g+delta)//2`
  （排除首、尾哨兵）累计；触及 cap 时保底压缩按最小相邻权重合并并显式累加。
  因此即使 cap 生效，真实误差也由该字段诚实给出。
* **可合并性**：同契约摘要按值归并（同值聚合、跨源 delta 平移），先按来源规模
  压缩、再按总规模压缩回 epsilon 契约。算法名 / epsilon / cap 任一不同即抛
  `IncompatibleSummary`（CLI 退出码 2），禁止混用。

## 层级索引与局部重建（实现要求 2）

固定 16 个源分区（编号 0–15），构造完整二叉树：叶 key 为 `"i:i+1"`，内部节点
为 `"a:b"`，全部区间半开，根为 `"0:16"`，共 31 个节点。

* **范围合并**：任意 `[start,end)` 先由 `canonical_cover` 分解为互不相交、key
  升序的规范节点覆盖，再折叠合并；`--reverse` 只反转折叠顺序，两种都是合法
  合并顺序。查询路径**不重扫原始样本**。
* **修订/删除**：取 revision 最大者；重复应用不重建；同 revision 负载冲突报错
  （退出码 1）；`values=null` 为整分区删除（墓碑）。变更时只从原始数据重建受
  影响叶摘要，再沿父链重建祖先，其余节点不触碰，实际重建集合经 `recomputed`
  返回并在测试中与“叶+祖先”精确核对。
* **快照可追溯**：每个摘要带参与分区 `revisions` 与内容哈希 `summary_id`
  （SHA-256，覆盖算法契约、元组、分区版本）；查询返回参与摘要的全部
  `participating_summary_ids` 与 `merged_summary_id`，可回答“来自哪次快照”。
* **稳定迁移**：`migrate` 只从磁盘原始数据重建，顺序固定为叶 key 升序，再自底
  向上、同层 key 升序（共 31 节点）。
* **磁盘布局**：`partitions_raw.json`（原始样本，不计入摘要内存）与
  `tree_summary.json`（31 节点摘要）。

## 查询输出（实现要求 3）

`query` 返回 `count`、`quantiles`（50/95/99）、每个分位的 `target_ranks` 与全
局 `rank_error_bound`、参与分区 `revisions`、覆盖分片 `cover`、
`participating_summary_ids`、`merged_summary_id`、`algorithm`/`epsilon`、
`max_summary_items`、`summary_item_cap` 与 `memory_bytes`。空样本量时分位数均
为 `null`，空分区 revision 不出现在 `revisions`。

内存记账约定：每条加权记录 `BYTES_PER_RECORD=160` 字节（CPython 3 保守值），
`stats` 给出节点数、总记录数、最大单节点记录数与总内存；任何驻留节点及查询
合并摘要的记录数都不超过 4096。

## 验收（对应验收标准）

1. 在均匀、长尾（Pareto）、大量重复值（12 个取值）三类样本量不均的输入上，用
   精确全量排序核对 P50/P95/P99，绝对秩误差 ≤ 1% N；对升序、降序、跨步三种
   合法合并顺序分别核对（`tests/test_acceptance.py`、`tools/run_acceptance.py`）。
2. 回放稀疏更正与整分区删除，按精确排序重算受影响范围，核对查询样本数、参与
   版本、实际重建节点集合（== 叶+祖先），并检查每节点记录数 ≤ 4096 的内存上界。
3. 固定种子（`SEED=20261114`）重复运行逐字节比较全部节点 `summary_id`，保证
   可重复；数据集可用 `tools/make_bench.py KIND` 导出。

## 退出码

* `0` 成功；`2` 未知算法或算法混用；`1` revision 冲突、区间越界、32 位整数越界
  等其他业务错误。

