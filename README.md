# 渐进三角网格

Python 3.9+。运行 `python3 progressive.py encode demo.json stream.json` 和 `python3 progressive.py decode stream.json out.json --level 1`。输入 {mesh,targets,max_error}，mesh 有 vertices=[{id,p:[x,y,z]}] 和 faces=[{id,v:[三个顶点 ID],uv:[三个逐角 UV],material}]。顶点和面 ID 唯一，面绕序有意义。targets 为由小到大的目标面数，max_error 为允许的几何误差。保留边界环、材质/UV 接缝和硬边；合法折叠不能使三角形翻面或产生重复面、非流形边。可在需要时提前停止并说明原因。

stream 至少含 base（最粗网格）、splits（从粗到细的局部可逆记录）与 levels（按粗到细的 {count,split}，split 为要回放的记录数量）。最后一级完全恢复原 mesh 的顶点、面 ID 与逐角属性。允许附加随机访问检查点及统计。decode 的 level 为 levels 零基下标。输出 vertices/faces 按 ID 排序，levels 的 count 真实等于解码面数。输入顶点或面列表重排不应改变规范化层级。所有样例为合成网格。

## 实现说明（progressive.py）

- 受保护顶点（开放边界、材质边界、UV 接缝、二面角超过 hard_edge_angle 的硬边，默认 45 度）不被移除也不移动；受保护幸存者的折叠位置固定在原处。折叠候选为 QEM 代价最小的有向边，平移候选取端点/中点；同代价按 (cost, u, v) 字典序出堆，结果与输入列表顺序无关。
- 每次折叠前检查 link condition、法线翻转（点积阈值）、退化与重复面；不满足即丢弃该候选。
- splits 记录 {u,v,pu,pv,rm,add}：回放时删除 rm 面、恢复 u/v 位置、按稳定 ID 加回 add 中的原始面记录（含逐角 UV 与材质）。levels 附带该层几何误差上界 error（不超过 max_error）。diffs 给出 base→第 0 级及相邻层的 splits 区间 [begin,end)。
- stats.stop 说明停止原因：targets_reached / max_error（最便宜的合法折叠超出误差）/ no_legal_collapse（无满足约束的候选），unreachable 列出不可达目标。decode 默认 --level -1（最后一级，即完整原网格）。
- 运行 `python3 verify.py` 执行验收：带洞薄片与折叠网格的连通分量/边界环/法线方向、层级误差上界、末级逐项一致、输入重排确定性、固定种子 30 次前进回退与独立载入对比、两类停止原因。
