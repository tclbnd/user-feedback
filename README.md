# 腕部蒙皮修复

`python3 skin.py input.json output.json`。输入 `bones` 数组，每项有字符串 `id`、单位旋转四元数 `rotation`（w、x、y、z）及三维 `translation`，均为从绑定姿态到当前姿态的刚体变换。`vertices` 数组每项含三维 `position` 和 `weights`（`[{"bone":"root","weight":0.6}, ...]`）。权重非负。没有有效权重时保留绑定位置。混合四元数的符号锚优先取最大权重骨骼，同权重按骨骼 ID 排序；内积为零时选择输入四元数的首个非零分量为正的表示。

输出 `vertices`（按原顶点顺序，每项 `position`、`method`），`stats.max_weight_sum_error` 及 `digest`。摘要应随最终位置变化且在重排骨骼表和权重表后保持一致。骨骼和位置均按右手坐标系解释。

## 实现

线性混合蒙皮对骨骼矩阵做加权平均，大扭转角下平均矩阵不再正交，截面被压向扭转轴，形成糖纸状塌陷。本实现改为双四元数蒙皮：

1. 每个骨骼的刚体变换写成单位对偶四元数 `(q_r, q_d)`，`q_d = 0.5 * (0, t) * q_r`；
2. 以最大权重骨骼（同权重按 ID）为符号锚，逐骨骼做最短路径半球对齐，按权重线性混合并对旋转部分重新归一化；
3. 由混合结果还原刚体变换并应用到顶点，因此截面保持刚性、周长不会被压缩。

边界情况：

- 权重和为零（或所有权重被过滤为 0）的顶点沿用绑定位置，`method` 为 `bind`；
- 只剩一根骨骼的退化顶点直接应用该骨骼变换，与线性混合逐位一致；
- 权重先按最大值缩放做归一化并吸附到 `2**-53` 网格，保证 `math.fsum` 求和恰为 1，`max_weight_sum_error` 为 0；
- 同一骨骼的重复权重项会合并，非正、非有限权重被丢弃；
- 权重按 (权重降序, 骨骼 ID) 规范化，因此骨骼表与权重表的顺序不影响任何输出。

`stats` 另外给出 `method_counts`、`bind_vertices`、`degenerate_single_bone` 以及逐位置统计 `position_min`、`position_max`、`position_centroid`、`max_displacement`。`digest` 仍是最终位置列表的 SHA-256。

## 测试

`python3 -m unittest discover -s tests`。覆盖 180°/270° 扭转下的周长保留率、与独立双四元数参考实现的误差、静止姿态逐位一致性、零权重/单骨退化顶点、权重和归一化，以及随机重排骨骼与权重顺序 8 次后的摘要一致性。
