# 可回放实体关系

Python 3.9+，无第三方依赖。

```
python3 entities.py apply state.json demo.json [--upto N]
python3 entities.py inspect state.json [--version V] [--audit]
python3 -m unittest test_entities -v
```

## 输入模型

`nodes` 为稳定、唯一的字符串节点。`links` 按数组顺序给出，每条含全局唯一
`event`、`a`、`b`、`source`、`kind`（`same` 或 `conflict`）、`action`
（`set` 或 `retract`）。边键为无向端点、`source` 和 `kind`，因此不同来源
的活动边并存；撤回只移除指定来源，同端点对仍有其它活动来源时边不消失。

- 重复 `event` 且归一化载荷等价：幂等忽略，不推进逻辑版本，因此整文件重跑、
  分块累积以及重放部分边都得到同一状态。
- 重复 `event` 但载荷冲突：CLI 以退出码 2 报错且不写回状态（原状态文件保留）。
- 未知节点、非法 `kind/action`、`--upto` 越界、`nodes` 与已提交状态不一致
  同样以退出码 2 拒绝并保留状态。
- 每个被接受的输入位置推进一个逻辑版本，初始版本为 0。`--upto N` 只消费
  当前输入文件的前 N 条；分块回放时把多个文件依次 `apply` 到同一状态文件，
  幂等去重保证结果与一次性全量回放一致。

## 划分与冲突优先级

每个节点初始独立成组。按归一化 `(a,b)` 字典序扫描存在任一活动 `same`
来源的边（支持边）。只要两个群的合并会把任一活动 `conflict` 对放进同一群，
就拒绝该合并，并在审计 `blocked_merges` 中记录被拒边与决定性冲突边。
冲突边插入会立即约束其所在连通区域：区域重算时历史合并可能被新冲突拆开；
冲突撤回后约束消失，下一次重算按相同扫描顺序重新合并。

输出中的 `blocked_links` 是当前存在任一活动冲突来源的端点对
（`[[a,b],...]`，按字典序）。

## 实体 ID 继承、别名与拆分

分组成员升序；群 ID 为 `entity-` 加组内最小成员（锚节点），由最小锚继承。
合并到更小锚时，被归并群的旧 ID 成为别名，指向当前拥有该锚的群；拆分时
锚恢复自己的独立 ID（对应别名撤销），若锚落入别的群则别名 `repoint` 到
新主 ID。只有曾经作为群 ID 出现过的节点才可能成为别名。别名输出为
`[{from,to},...]` 按 `from` 排序；完整的 `add/revoke/repoint` 历史及生效
版本保存在状态文件与 `--audit` 的 `alias_history` 中。

## 增量重算

维护活动支持图的邻接表。每条事件只在其端点所在连通区域内重算
（撤回时种子覆盖断开后的两侧），区域外节点的实体 ID 与归属不变；区域内
求解与全量求解共用同一套确定性规则，因此增量结果与从头重放逐版本一致。
`region_log` 记录每版本实际重算的区域，便于核对“小群操作不波及其它群”。

## 输出与审计视图

`inspect` / `apply` 输出
`{version,clusters,aliases,blocked_links,changes}`，`clusters` 为
`[{id,members},...]` 按 id 排序。`changes` 仅记录有归属变化的版本，形式为
`{version,move:[{node,from,to},...]}`，`move` 按 node 排序，初始版本不记录。

`inspect --audit [--version V]` 给出任意已提交版本的追溯视图：

- `clusters[].support_records`：群内每条活动支持边及其来源、输入 event；
- `clusters[].decisive_edges`：确定性扫描中真正促成合并的边（多来源全部列出）；
- `clusters[].conflict_records`：群内活动冲突边及其来源 event；
- `membership_at`：该版本的完整成员清单；
- `blocked_merges`、`alias_history`、`region_log`、`changes`：对应历史明细。

## 验收测试（`test_entities.py`）

1. 手工场景覆盖链式合并、冲突边插入与撤回、桥边撤回、同边多来源；独立图
   求解器（frozenset 合并，独立实现）逐版本比较实体划分与 `blocked_links`，
   并以手工预期验证主 ID 继承、别名 `add/revoke/repoint` 与生效版本。
2. 40 组随机序列（含链式合并、撤回、冲突、多来源）逐版本对拍独立求解器，
   并交叉验证引擎自身的全量求解路径。
3. 4 种输入分块（整块、两块、三块、逐条）回放并在块内重复部分边，比较完整
   状态、实体 ID、别名与审计明细完全相同；整文件重跑输出逐字节一致。
4. 仅影响独立小群 `x-y` 的撤回：核对其它群 ID 与归属零变化，且重算区域
   只包含 `x/y`。
5. CLI 端到端：退出码 2、错误时状态保留、`--upto` 前缀与任意版本 inspect。

