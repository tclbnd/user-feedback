# 渐进三角网格

Python 3.9+，仅标准库。

```
python3 progressive.py encode demo.json stream.json
python3 progressive.py decode stream.json out.json --level 1
python3 progressive.py diff   stream.json prefix      # 写 prefix.level{k}.json / prefix.diff{k}.json
python3 test_progressive.py                           # 16 项验收测试
```

输入 `{mesh,targets,max_error}`，mesh 有 `vertices=[{id,p:[x,y,z]}]` 与
`faces=[{id,v:[三个顶点 ID],uv:[三个逐角 UV],material}]`，可选 `hard_edges`
显式硬边。顶点/面 ID 唯一，面绕序有意义。`targets` 为目标面数（任意顺序，内部排序）。

## 折叠约束（任一不满足即拒绝）

1. **材质**：折叠边两侧材质必须相同；折叠后新生成的边也不得成为新材质缝。
2. **UV 岛**：两端点逐角 UV 在两侧面必须一致；reroute 产生的新边不得制造 UV 缝。
3. **硬边**：边界边、非流形边、二面角大于 30°（`HARD_ANGLE`）的边及显式
   `hard_edges` 永不折叠；每次折叠后对受影响边重新分类，新折角会被即时锁死。
4. **开放边界**：边界顶点不参与任何折叠，边界环逐顶点保持。
5. **拓扑**：simplicial link 条件 + 重复面/退化面检测，拒绝非流形变化。
6. **朝向**：每个受影响面折叠前后法向点积为正，拒绝翻面与退化。
7. **误差预算**：QEM 折叠误差（最优/中点/端点四种候选点取最便宜的合法点）
   的平方根不得超过 `max_error`。

## 顶点拆分记录（可逐步回放）

每次折叠写一条自包含逆记录：`edge, removed, kept, removed_p, kept_old_p,
kept_new_p, deleted_faces[完整面快照], reroute[完整面快照+old/new 绕序],
seq, split_seq, error`。存储的 `splits` 是折叠顺序的逆序（粗→细拆分流），
`levels` 按粗→细给出 `{count,split}`，`split` 为要回放的拆分数。
`base` 为最粗网格；跳到某级 = base + 前 `split` 条记录；`diffs` 保存相邻层
增量。`apply_split` / `apply_inverse_split` 支持单步前进与回退，任意中间态
都等于该层的独立载入结果。原始顶点与面 ID 永不复用，最细级逐项恢复原 mesh
的 ID、绕序与逐角 UV/材质。

## 确定性

堆条目排序键为 `(QEM 误差, 候选点类型, 被折叠 ID, 保留 ID, 坐标)`，所有集合
遍历均先排序；浮点求和只依赖面/边 ID 顺序。打乱输入顶点或面列表产生字节级
相同的规范化 stream（测试覆盖 20 种打乱）。

## 无法继续简化时

`stop` 给出 `{stopped, reason, remaining_targets, face_count}`，reason 含
各类拒绝计数；最细级始终是原网格，已完成层级仍可正常解码。

## 验收（`test_progressive.py`，固定种子）

- 带洞薄片：1 个连通分量、边界环 12（洞）+48（外环）、法线同向，且每层保持。
- 3 个层级（170/220/270 面）与 2 个相邻层差分；`count` 与真实解码面数一致。
- 每层对称 Hausdorff 距离 ≤ `max_error`（实测 1.0 ≤ 1.01），逐条折叠误差留档。
- 回放到原层时顶点位置、面 ID、绕序、逐角 UV 与材质按稳定 ID 逐项相同。
- 固定种子 30 次单步前进/回退 + 10 次跳转后继续读增量，每次与独立载入比较。
- 90° 硬折边、材质缝、UV 缝、零预算停止原因、双连通分量、翻面/重复面拒绝、
  CLI 端到端产物各有专项测试。
