# 版本化分位数摘要

Python 3.9+。用 `python3 quantiles.py ROOT apply demo.json`、`python3 quantiles.py ROOT query --start 0 --end 16`。0–15 为 16 个源分区。apply 输入含 partitions，每项有 index、revision（正整数）、values（有符号 32 位整数数组，null 表示删除），和可选 algorithm（默认 q1）。取 revision 最大，重复不重建，同版本冲突报错，未知 algorithm 退出 2。可在 apply 内完成旧摘要迁移或拒绝混用，不能把原始样本当作摘要。不同批次的最终逻辑摘要结果应可重复。

构造完整的二叉分区树。叶 key 为 "i:i+1"，其他节点为 "a:b"，所有区间半开。只重建变更叶子与祖先。apply 返回 recomputed（重建节点 key 列表，顺序不限）。query 按 [start,end) 查询，由树中的覆盖区间合并摘要。--reverse 倒转合法覆盖分片的合并顺序。结果至少含 count、quantiles={"50":数值,"95":数值,"99":数值}、revisions（非空参与分区的 index 字符串到 revision）、max_summary_items（任何驻留单节点及合并摘要的加权记录条数上界，不含磁盘原始数据）、algorithm。空样本量时分位数均 null。允许空分区 revision 不出现在 revisions。

每个节点最多 4096 个加权记录。按最近秩定义计算 p 分位，目标秩 ceil(p×N)，并列值的秩视为其完整秩区间。按多组数据验收，误差不得超过 N 的 1%。输出实现可声明可证明的误差上界和内存统计。不得在 query 重扫原始样本。
