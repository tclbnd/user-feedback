# 腕部蒙皮修复

`python3 skin.py input.json output.json`。输入 `bones` 数组，每项有字符串 `id`、单位旋转四元数 `rotation`（w、x、y、z）及三维 `translation`，均为从绑定姿态到当前姿态的刚体变换。`vertices` 数组每项含三维 `position` 和 `weights`（`[{"bone":"root","weight":0.6}, ...]`）。权重非负。没有有效权重时保留绑定位置。混合四元数的符号锚优先取最大权重骨骼，同权重按骨骼 ID 排序；内积为零时选择输入四元数的首个非零分量为正的表示。

输出 `vertices`（按原顶点顺序，每项 `position`、`method`），`stats.max_weight_sum_error` 及 `digest`。摘要应随最终位置变化且在重排骨骼表和权重表后保持一致。骨骼和位置均按右手坐标系解释。
